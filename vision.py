"""
Vision / Image-Processing Module
Provides template matching, colour detection, OCR helpers, and region utilities
for analysing game screenshots.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import cv2
import numpy as np

import config

log = logging.getLogger(__name__)

# Region type: (x, y, w, h)
Region = Tuple[int, int, int, int]


# ─── Core helpers ─────────────────────────────────────────────────────────────

def crop(frame: np.ndarray, region: Region) -> np.ndarray:
    """Crop a BGR frame to a (x, y, w, h) region."""
    x, y, w, h = region
    return frame[y:y + h, x:x + w]


def to_gray(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def resize_to_fit(frame: np.ndarray, max_dim: int = 1080) -> np.ndarray:
    h, w = frame.shape[:2]
    if max(h, w) <= max_dim:
        return frame
    scale = max_dim / max(h, w)
    return cv2.resize(frame, (int(w * scale), int(h * scale)))


# ─── Template matching ────────────────────────────────────────────────────────

_template_cache: Dict[str, Optional[np.ndarray]] = {}


def load_template(path: str) -> Optional[np.ndarray]:
    """Load and cache a template image (grayscale). Returns None if missing."""
    if path not in _template_cache:
        if os.path.exists(path):
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            _template_cache[path] = img
            log.debug("Loaded template: %s", path)
        else:
            _template_cache[path] = None
            log.warning("Template not found: %s", path)
    return _template_cache[path]


def match_template(
    frame: np.ndarray,
    template_path: str,
    threshold: float = None,
    region: Optional[Region] = None,
) -> Optional[Tuple[float, Tuple[int, int]]]:
    """
    Search for a template image inside a frame (or a sub-region of it).

    Returns (score, (cx, cy)) of the best match if score >= threshold,
    otherwise None.  Coordinates are in full-frame space.
    """
    tpl = load_template(template_path)
    if tpl is None:
        return None

    thr = threshold if threshold is not None else config.MATCH_THRESHOLD

    search_area = frame
    offset_x, offset_y = 0, 0
    if region:
        search_area = crop(frame, region)
        offset_x, offset_y = region[0], region[1]

    gray_area = to_gray(search_area)

    # Scale template if needed
    th, tw = tpl.shape[:2]
    ah, aw = gray_area.shape[:2]
    if tw > aw or th > ah:
        log.debug("Template larger than search area; skipping.")
        return None

    result = cv2.matchTemplate(gray_area, tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= thr:
        cx = max_loc[0] + tw // 2 + offset_x
        cy = max_loc[1] + th // 2 + offset_y
        log.debug("Template '%s' matched %.2f at (%d, %d)", template_path, max_val, cx, cy)
        return max_val, (cx, cy)
    return None


def find_all_matches(
    frame: np.ndarray,
    template_path: str,
    threshold: float = None,
) -> List[Tuple[float, Tuple[int, int]]]:
    """Find all non-overlapping occurrences of a template in the frame."""
    tpl = load_template(template_path)
    if tpl is None:
        return []

    thr = threshold if threshold is not None else config.MATCH_THRESHOLD
    gray = to_gray(frame)
    th, tw = tpl.shape[:2]
    result = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)

    locations = np.where(result >= thr)
    matches = []
    for pt in zip(*locations[::-1]):  # (x, y)
        score = float(result[pt[1], pt[0]])
        cx = pt[0] + tw // 2
        cy = pt[1] + th // 2
        matches.append((score, (cx, cy)))

    # Non-maximum suppression (basic)
    matches.sort(key=lambda m: -m[0])
    kept = []
    for score, (cx, cy) in matches:
        if all(abs(cx - kx) > tw // 2 or abs(cy - ky) > th // 2
               for _, (kx, ky) in kept):
            kept.append((score, (cx, cy)))
    return kept


# ─── Colour detection ─────────────────────────────────────────────────────────

def detect_color_region(
    frame: np.ndarray,
    lower_bgr: Tuple[int, int, int],
    upper_bgr: Tuple[int, int, int],
    region: Optional[Region] = None,
    min_pixel_count: int = 50,
) -> bool:
    """Return True if a colour range is present in the frame (or region)."""
    area = crop(frame, region) if region else frame
    lower = np.array(lower_bgr, dtype=np.uint8)
    upper = np.array(upper_bgr, dtype=np.uint8)
    mask = cv2.inRange(area, lower, upper)
    return int(mask.sum() // 255) >= min_pixel_count


def find_color_centroid(
    frame: np.ndarray,
    lower_bgr: Tuple[int, int, int],
    upper_bgr: Tuple[int, int, int],
    region: Optional[Region] = None,
) -> Optional[Tuple[int, int]]:
    """Return the centroid (x, y) of the largest colour blob, or None."""
    area = crop(frame, region) if region else frame
    lower = np.array(lower_bgr, dtype=np.uint8)
    upper = np.array(upper_bgr, dtype=np.uint8)
    mask  = cv2.inRange(area, lower, upper)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    M = cv2.moments(largest)
    if M["m00"] == 0:
        return None

    cx = int(M["m10"] / M["m00"])
    cy = int(M["m01"] / M["m00"])
    if region:
        cx += region[0]
        cy += region[1]
    return cx, cy


# ─── OCR (optional — requires pytesseract + tesseract-ocr installed) ──────────

def ocr_region(
    frame: np.ndarray,
    region: Optional[Region] = None,
    lang: str = "eng",
    config_str: str = "--psm 6",
) -> str:
    """
    Extract text from a frame region using Tesseract OCR.
    Returns empty string if pytesseract is not installed.
    """
    try:
        import pytesseract
    except ImportError:
        return ""

    area = crop(frame, region) if region else frame
    gray = to_gray(area)
    # Upscale for better OCR accuracy
    if gray.shape[0] < 100:
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    text = pytesseract.image_to_string(gray, lang=lang, config=config_str)
    return text.strip().lower()


def find_text_in_region(
    frame: np.ndarray,
    keywords: List[str],
    region: Optional[Region] = None,
) -> bool:
    """Return True if any keyword appears in the OCR text of a region."""
    text = ocr_region(frame, region)
    return any(kw.lower() in text for kw in keywords)


# ─── Utility ──────────────────────────────────────────────────────────────────

def frames_are_same(
    f1: np.ndarray,
    f2: np.ndarray,
    diff_threshold: float = 0.02,
) -> bool:
    """Return True if two frames look essentially identical (< diff_threshold % changed)."""
    if f1.shape != f2.shape:
        return False
    diff = cv2.absdiff(f1, f2)
    changed = np.count_nonzero(diff.sum(axis=2))
    ratio = changed / (f1.shape[0] * f1.shape[1])
    return ratio < diff_threshold


def save_debug(frame: np.ndarray, name: str) -> None:
    """Save a debug screenshot if SAVE_DEBUG_SCREENSHOTS is enabled."""
    if not config.SAVE_DEBUG_SCREENSHOTS:
        return
    Path(config.DEBUG_DIR).mkdir(parents=True, exist_ok=True)
    import time
    ts  = int(time.time() * 1000)
    path = os.path.join(config.DEBUG_DIR, f"{ts}_{name}.png")
    cv2.imwrite(path, frame)
    log.debug("Debug screenshot: %s", path)


def draw_debug_overlay(
    frame: np.ndarray,
    label: str,
    regions: List[Region] = None,
    points: List[Tuple[int, int]] = None,
) -> np.ndarray:
    """Return a copy of frame with debug drawings."""
    out = frame.copy()
    if regions:
        for x, y, w, h in regions:
            cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 2)
    if points:
        for px, py in points:
            cv2.circle(out, (px, py), 10, (0, 0, 255), -1)
    cv2.putText(out, label, (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 0), 2)
    return out
