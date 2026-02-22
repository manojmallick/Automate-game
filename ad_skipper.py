"""
Ad Skipper
Detects and dismisses full-screen interstitial ads, banner ads, reward-video
skip buttons, and various ad SDK overlays that appear during gameplay.

Strategy (in order):
1. Template match against known ad overlay screenshots
2. Scan hotspot regions for a "×" / "Skip" close button by colour heuristics
3. OCR scan for ad-related text keywords
4. Fallback: tap common close-button positions
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

# Known close-button colours in BGR
# White × on dark background
_WHITE_LOWER = np.array([180, 180, 180], dtype=np.uint8)
_WHITE_UPPER = np.array([255, 255, 255], dtype=np.uint8)

# Red / orange "×"
_RED_LOWER = np.array([0,   0, 160], dtype=np.uint8)
_RED_UPPER = np.array([80, 80, 255], dtype=np.uint8)

# Yellow "Skip" button (common in reward ads)
_YELLOW_LOWER = np.array([0,  180, 180], dtype=np.uint8)
_YELLOW_UPPER = np.array([80, 255, 255], dtype=np.uint8)

# Scan grid: divide screen into regions that might contain close buttons
_CLOSE_BUTTON_HOTSPOTS = [
    # (x, y, w, h)
    # Top-right (most common for interstitials)
    (880,  40, 200, 200),
    # Top-left
    (0,    40, 200, 200),
    # Top-centre
    (390,  40, 300, 200),
    # Bottom-right
    (880, 2100, 200, 200),
    # Bottom-centre ("Skip Ad" bar)
    (270, 2050, 540, 200),
    # Mid-right (some reward video SDKs)
    (880,  900, 200, 200),
]

# Fallback tap positions when no button is visually located
_FALLBACK_TAPS = [
    (960, 80),    # top-right corner
    (120, 80),    # top-left corner
    (540, 2150),  # bottom-centre "Skip"
    (960, 2150),  # bottom-right
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
        # 1. Template match
        if config.TEMPLATES.get("ad_overlay"):
            match = vision.match_template(frame, config.TEMPLATES["ad_overlay"], threshold=0.70)
            if match:
                log.info("Ad detected via template match (score=%.2f)", match[0])
                return True

        # 2. Colour heuristic: look for the tell-tale small "×" button region
        for region in _CLOSE_BUTTON_HOTSPOTS:
            pos = self._find_close_button_in_region(frame, region)
            if pos:
                log.info("Ad detected via close-button colour in region %s", region)
                return True

        # 3. OCR on top strip (many ad SDKs show "Advertisement" / "Ad" label)
        top_strip = (0, 0, config.SCREEN_WIDTH, 160)
        if vision.find_text_in_region(frame, ["advertisement", " ad "], top_strip):
            log.info("Ad detected via OCR top-strip label")
            return True

        return False

    def dismiss_ad(self, frame: np.ndarray) -> bool:
        """
        Try to dismiss a visible ad.  Returns True if a dismiss tap was made.
        """
        log.info("Attempting to dismiss ad…")

        # 1. Look for close button by colour in each hotspot
        for region in _CLOSE_BUTTON_HOTSPOTS:
            pos = self._find_close_button_in_region(frame, region)
            if pos:
                log.info("Tapping close button at (%d, %d)", *pos)
                self.adb.tap(*pos, delay=0.8)
                self._skips += 1
                return True

        # 2. OCR-guided tap
        for region in _CLOSE_BUTTON_HOTSPOTS:
            if vision.find_text_in_region(frame, _SKIP_KEYWORDS, region):
                cx = region[0] + region[2] // 2
                cy = region[1] + region[3] // 2
                log.info("Tapping OCR skip keyword region centre (%d, %d)", cx, cy)
                self.adb.tap(cx, cy, delay=0.8)
                self._skips += 1
                return True

        # 3. Template-matched close button (ad_overlay template)
        if config.TEMPLATES.get("ad_overlay"):
            match = vision.match_template(frame, config.TEMPLATES["ad_overlay"], threshold=0.65)
            if match:
                _, (cx, cy) = match
                log.info("Tapping template-matched overlay at (%d, %d)", cx, cy)
                self.adb.tap(cx, cy, delay=0.8)
                self._skips += 1
                return True

        # 4. Fallback: try all known close-button positions
        log.warning("No close button found; trying fallback tap sequence")
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
            # Wait for ad animation to finish, then take a fresh check
            time.sleep(1.5)
            return True
        return False

    def skip_reward_video(self, max_wait: int = 35) -> bool:
        """
        Wait for a reward-video ad to finish or become skippable, then dismiss.
        Polls every second for a skip button up to max_wait seconds.
        Returns True if skipped successfully.
        """
        log.info("Waiting for reward video skip button (max %ds)…", max_wait)
        for _ in range(max_wait):
            frame = self.adb.screenshot()
            for region in _CLOSE_BUTTON_HOTSPOTS:
                pos = self._find_close_button_in_region(frame, region)
                if pos:
                    log.info("Skip button appeared at (%d, %d)", *pos)
                    self.adb.tap(*pos, delay=1.0)
                    self._skips += 1
                    return True
                if vision.find_text_in_region(frame, _SKIP_KEYWORDS, region):
                    cx = region[0] + region[2] // 2
                    cy = region[1] + region[3] // 2
                    self.adb.tap(cx, cy, delay=1.0)
                    self._skips += 1
                    return True
            time.sleep(1.0)

        # Force-tap after timeout
        log.warning("Reward video did not become skippable; force-tapping")
        for tx, ty in _FALLBACK_TAPS:
            self.adb.tap(tx, ty, delay=0.4)
        self._skips += 1
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
        """
        x, y, w, h = region
        if y + h > frame.shape[0] or x + w > frame.shape[1]:
            return None
        patch = frame[y:y + h, x:x + w]

        for lower, upper in [
            (_WHITE_LOWER, _WHITE_UPPER),
            (_RED_LOWER,   _RED_UPPER),
            (_YELLOW_LOWER, _YELLOW_UPPER),
        ]:
            mask = cv2.inRange(patch, lower, upper)
            count = int(mask.sum() // 255)
            if 8 <= count <= 3000:  # small button, not full background
                # Find centroid
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
