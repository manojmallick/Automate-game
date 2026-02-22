"""
Ad Skipper
Detects and dismisses full-screen interstitial ads, banner ads, reward-video
skip buttons, and various ad SDK overlays that appear during gameplay.

Strategy (in order):
1. Template match against known ad overlay screenshots
2. Scan hotspot regions for a "×" / "Skip" close button by colour heuristics
3. OCR scan for ad-related text keywords
4. Fallback: tap common close-button positions

Key behaviour
─────────────
dismiss_ad() WAITS for the skip/close button to become visible (ads often
delay the close button by 5 seconds) before tapping.  It polls the screen
every second up to config.AD_SKIP_WAIT_SECONDS.
"""

import logging
import time
from typing import Optional, Tuple

import cv2
import numpy as np

import config
from adb_controller import ADBController
import vision

log = logging.getLogger(__name__)

# ─── Colour ranges for typical ad close/skip buttons (BGR) ───────────────────

# White "×" on dark circular background (most common)
_WHITE_LOWER = np.array([180, 180, 180], dtype=np.uint8)
_WHITE_UPPER = np.array([255, 255, 255], dtype=np.uint8)

# Red / orange "×"
_RED_LOWER = np.array([0,   0, 150], dtype=np.uint8)
_RED_UPPER = np.array([80, 80, 255], dtype=np.uint8)

# Yellow/gold "Skip" button (reward ads)
_YELLOW_LOWER = np.array([0,  160, 160], dtype=np.uint8)
_YELLOW_UPPER = np.array([80, 255, 255], dtype=np.uint8)

_COLOR_RANGES = [
    (_WHITE_LOWER,  _WHITE_UPPER),
    (_RED_LOWER,    _RED_UPPER),
    (_YELLOW_LOWER, _YELLOW_UPPER),
]

# Scan grid: regions that might contain close buttons (x, y, w, h)
_CLOSE_BUTTON_HOTSPOTS = [
    (860,   30, 220, 220),   # top-right  (most common for interstitials)
    (0,     30, 220, 220),   # top-left
    (380,   30, 320, 180),   # top-centre
    (270, 2050, 540, 200),   # bottom-centre "Skip Ad" bar
    (840, 2050, 240, 200),   # bottom-right
    (860,  860, 220, 220),   # mid-right   (some reward-video SDKs)
]

# Fallback tap positions used ONLY after max wait time expires
_FALLBACK_TAPS = [
    (1020,  80),   # top-right corner
    (60,    80),   # top-left corner
    (540,  2150),  # bottom-centre "Skip"
    (960,  2150),  # bottom-right
]

# Text that appears inside/near ad close buttons
_SKIP_KEYWORDS = [
    "skip", "skip ad", "close", "x", "got it",
    "no thanks", "dismiss", "continue", "resume", "done",
]

# Known ad SDK package names (to detect foreign packages in foreground)
AD_PACKAGES = {
    "com.google.android.gms.ads",
    "com.unity3d.ads",
    "com.ironsource.mediationsdk",
    "com.applovin",
    "com.chartboost",
    "com.vungle",
    "com.facebook.ads",
    "com.inmobi",
    "air.com.adcolony",
}


class AdSkipper:
    """
    Detects and dismisses ads on the device screen.
    """

    def __init__(self, adb: ADBController):
        self.adb    = adb
        self._skips = 0   # total ads dismissed this session

    # ─── Public API ──────────────────────────────────────────────────────────

    def is_ad_showing(self, frame: np.ndarray) -> bool:
        """
        Heuristic check: return True if the current frame looks like an ad.
        Combines template matching + colour analysis + OCR.
        """
        # 1. Template match (exact — very fast)
        if config.TEMPLATES.get("ad_overlay"):
            match = vision.match_template(
                frame, config.TEMPLATES["ad_overlay"], threshold=0.70
            )
            if match:
                log.info("Ad detected via template (score=%.2f)", match[0])
                return True

        # 2. Colour heuristic: look for the tell-tale small "×" button region
        for region in _CLOSE_BUTTON_HOTSPOTS:
            pos = self._find_close_button_in_region(frame, region)
            if pos:
                log.info("Ad close button colour-detected in region %s", region)
                return True

        # 3. OCR on top strip (many ad SDKs show "Advertisement" / "Ad" label)
        top_strip = (0, 0, config.SCREEN_WIDTH, 160)
        if vision.find_text_in_region(frame, ["advertisement", " ad "], top_strip):
            log.info("Ad detected via OCR top-strip label")
            return True

        return False

    def dismiss_ad(self, frame: np.ndarray) -> bool:
        """
        Wait for the skip/close button to appear, then tap it.

        Many interstitial and reward-video ads force users to watch for 5 s
        before the close button is shown.  This method polls once per second
        for up to config.AD_SKIP_WAIT_SECONDS before falling back to blind taps.

        Returns True once a dismiss tap has been made.
        """
        max_wait = config.AD_SKIP_WAIT_SECONDS
        log.info("Ad detected — waiting up to %ds for skip/close button…", max_wait)

        for attempt in range(max_wait):
            # Re-capture the screen on every iteration after the first
            if attempt > 0:
                try:
                    frame = self.adb.screenshot()
                except Exception as exc:
                    log.warning("Screenshot failed during ad wait: %s", exc)
                    time.sleep(1.0)
                    continue

            # ── 1. Colour-based close button search ──────────────────────────
            for region in _CLOSE_BUTTON_HOTSPOTS:
                pos = self._find_close_button_in_region(frame, region)
                if pos:
                    log.info(
                        "Skip/close button at (%d, %d) — tapping (attempt %d)",
                        pos[0], pos[1], attempt + 1,
                    )
                    self.adb.tap(*pos, delay=0.8)
                    self._skips += 1
                    time.sleep(0.5)   # brief pause for ad-dismiss animation
                    return True

            # ── 2. OCR-guided close button search ────────────────────────────
            for region in _CLOSE_BUTTON_HOTSPOTS:
                if vision.find_text_in_region(frame, _SKIP_KEYWORDS, region):
                    cx = region[0] + region[2] // 2
                    cy = region[1] + region[3] // 2
                    log.info(
                        "OCR skip keyword in region %s — tapping (%d, %d) (attempt %d)",
                        region, cx, cy, attempt + 1,
                    )
                    self.adb.tap(cx, cy, delay=0.8)
                    self._skips += 1
                    time.sleep(0.5)
                    return True

            # ── 3. Template-matched close button ─────────────────────────────
            if config.TEMPLATES.get("ad_overlay"):
                match = vision.match_template(
                    frame, config.TEMPLATES["ad_overlay"], threshold=0.65
                )
                if match:
                    _, (cx, cy) = match
                    log.info(
                        "Template-matched close button at (%d, %d) (attempt %d)",
                        cx, cy, attempt + 1,
                    )
                    self.adb.tap(cx, cy, delay=0.8)
                    self._skips += 1
                    time.sleep(0.5)
                    return True

            log.debug(
                "Close button not visible yet (attempt %d/%d) — waiting 1 s…",
                attempt + 1, max_wait,
            )
            time.sleep(1.0)

        # ── 4. Fallback after timeout ─────────────────────────────────────────
        log.warning(
            "Skip button did not appear within %d s — trying fallback taps", max_wait
        )
        for tx, ty in _FALLBACK_TAPS:
            self.adb.tap(tx, ty, delay=0.5)
        self._skips += 1
        return True

    def handle_if_ad(self, frame: np.ndarray) -> bool:
        """
        One-shot: detect ad and dismiss it.  Returns True if ad was handled.
        """
        if self.is_ad_showing(frame):
            self.dismiss_ad(frame)
            time.sleep(1.0)
            return True
        return False

    @property
    def total_skips(self) -> int:
        return self._skips

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _find_close_button_in_region(
        self, frame: np.ndarray, region: vision.Region
    ) -> Optional[Tuple[int, int]]:
        """
        Search a hotspot region for a close-button-shaped blob.
        Returns (x, y) in full-frame coordinates, or None.

        A close button is expected to be a small compact blob (8–3 000 px)
        in white, red, or yellow — far smaller than a full background fill.
        """
        x, y, w, h = region
        if y + h > frame.shape[0] or x + w > frame.shape[1]:
            return None
        patch = frame[y : y + h, x : x + w]

        for lower, upper in _COLOR_RANGES:
            mask  = cv2.inRange(patch, lower, upper)
            count = int(mask.sum() // 255)

            # Blob must be small (button) but non-trivial
            if 8 <= count <= 3000:
                M = cv2.moments(mask)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"]) + x
                    cy = int(M["m01"] / M["m00"]) + y
                    return cx, cy

        return None

    def _is_ad_package_in_foreground(self) -> bool:
        """Return True if an ad SDK package has taken foreground."""
        pkg = self.adb.current_package()
        return any(ad_pkg in pkg for ad_pkg in AD_PACKAGES)
