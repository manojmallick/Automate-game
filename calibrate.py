"""
Calibration Tool
Interactive helper that lets you:
  1. Capture a live screenshot from the device
  2. Click on the image to record tap coordinates
  3. Save those coordinates into config.py automatically
  4. Capture template images for game-state detection

Usage:
    python calibrate.py --mode tap         # record tap positions
    python calibrate.py --mode template    # capture template screenshots
    python calibrate.py --mode screenshot  # just save a screenshot
"""

import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np

from adb_controller import ADBController
import config


TEMPLATE_DIR = Path("templates")

# Colours
RED    = (0,   0, 255)
GREEN  = (0, 255,   0)
YELLOW = (0, 255, 255)
WHITE  = (255, 255, 255)


class Calibrator:
    def __init__(self):
        self.adb   = ADBController()
        self.frame: np.ndarray = None
        self.clicks = []

    def grab(self) -> np.ndarray:
        print("Capturing screenshot…")
        self.adb.ensure_connected()
        self.frame = self.adb.screenshot()
        print(f"  Screen size: {self.frame.shape[1]}×{self.frame.shape[0]}")
        return self.frame

    # ─── Tap calibration ─────────────────────────────────────────────────────

    def run_tap_calibration(self):
        """
        Show the live screenshot; user clicks to record coordinates.
        Each click is labelled with its index.
        """
        WINDOW = "Tap Calibration  (click to record, Q to quit, S to save)"
        frame   = self.grab()
        display = frame.copy()
        self.clicks = []

        def on_click(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                # Map display coords back to original if scaled
                sx = x * frame.shape[1] // display.shape[1]
                sy = y * frame.shape[0] // display.shape[0]
                self.clicks.append((sx, sy))
                idx = len(self.clicks)
                cv2.circle(display, (x, y), 14, RED, -1)
                cv2.putText(display, str(idx), (x + 16, y + 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, WHITE, 2)
                print(f"  Click #{idx}: ({sx}, {sy})")
                cv2.imshow(WINDOW, display)

        display = _scale_for_display(display)
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.imshow(WINDOW, display)
        cv2.setMouseCallback(WINDOW, on_click)

        print("\nClick on each button/area you want to calibrate.")
        print("Keys:  Q = quit,  S = save coords,  R = redo (clear)")

        while True:
            key = cv2.waitKey(50) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("s"):
                self._print_coords()
            elif key == ord("r"):
                self.clicks.clear()
                display[:] = _scale_for_display(frame)
                cv2.imshow(WINDOW, display)
                print("Cleared. Start over.")

        cv2.destroyAllWindows()
        self._print_coords()

    def _print_coords(self):
        print("\n── Recorded coordinates ─────────────────────────────────")
        labels = list(config.GAME_TAPS.keys())
        for i, (x, y) in enumerate(self.clicks):
            label = labels[i] if i < len(labels) else f"tap_{i + 1}"
            print(f'    "{label}": ({x}, {y}),')
        print("─────────────────────────────────────────────────────────")
        print("Paste the above into config.py  →  GAME_TAPS = { … }\n")

    # ─── Template capture ─────────────────────────────────────────────────────

    def run_template_capture(self):
        """
        For each game state, display the screen and let the user drag a
        rectangle to crop out the template region.
        """
        TEMPLATE_DIR.mkdir(exist_ok=True)
        states = list(config.TEMPLATES.keys())

        print(f"\nWill capture templates for: {states}")
        print("Instructions:")
        print("  1. Navigate the game to the desired screen BEFORE pressing ENTER")
        print("  2. Drag a rectangle over the unique UI region")
        print("  3. Press S to save, N to skip, Q to quit\n")

        for state_name in states:
            dest = config.TEMPLATES[state_name]
            if os.path.exists(dest):
                print(f"  [{state_name}] Template already exists ({dest}). Skip? [Y/n] ", end="")
                if input().strip().lower() != "n":
                    continue

            input(f"  Navigate to '{state_name}' screen, then press ENTER…")
            frame   = self.grab()
            display = _scale_for_display(frame)

            roi = cv2.selectROI(
                f"Select region for: {state_name}  (ENTER=confirm, C=cancel)",
                display,
                showCrosshair=True,
                fromCenter=False,
            )
            cv2.destroyAllWindows()

            if roi == (0, 0, 0, 0):
                print(f"  Skipped {state_name}")
                continue

            # Scale ROI back to original resolution
            sx = frame.shape[1] / display.shape[1]
            sy = frame.shape[0] / display.shape[0]
            rx = int(roi[0] * sx)
            ry = int(roi[1] * sy)
            rw = int(roi[2] * sx)
            rh = int(roi[3] * sy)
            crop = frame[ry:ry + rh, rx:rx + rw]

            import cv2 as _cv2
            _cv2.imwrite(dest, _cv2.cvtColor(crop, _cv2.COLOR_BGR2GRAY))
            print(f"  Saved template: {dest}  ({rw}×{rh}px)")

        print("\nTemplate capture complete.")

    # ─── Simple screenshot ────────────────────────────────────────────────────

    def run_screenshot(self, output: str = "screenshot.png"):
        frame = self.grab()
        import cv2 as _cv2
        _cv2.imwrite(output, frame)
        print(f"Screenshot saved: {output}")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _scale_for_display(frame: np.ndarray, max_h: int = 900) -> np.ndarray:
    h, w = frame.shape[:2]
    if h <= max_h:
        return frame.copy()
    scale = max_h / h
    return cv2.resize(frame, (int(w * scale), int(h * scale)))


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Game Bot Calibration Tool")
    parser.add_argument(
        "--mode", choices=["tap", "template", "screenshot"], default="tap",
        help="Calibration mode"
    )
    parser.add_argument("--output", default="screenshot.png",
                        help="Output file for screenshot mode")
    args = parser.parse_args()

    cal = Calibrator()
    try:
        cal.adb.ensure_connected()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if args.mode == "tap":
        cal.run_tap_calibration()
    elif args.mode == "template":
        cal.run_template_capture()
    elif args.mode == "screenshot":
        cal.run_screenshot(args.output)


if __name__ == "__main__":
    main()
