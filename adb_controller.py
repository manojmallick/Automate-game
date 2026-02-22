"""
ADB Controller
Wraps all Android Debug Bridge interactions: connect, screenshot, tap, swipe,
key-events, and text input.
"""

import io
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image

import config

log = logging.getLogger(__name__)


class ADBError(RuntimeError):
    pass


class ADBController:
    """Thin wrapper around the adb CLI tool."""

    def __init__(self, device: Optional[str] = None, adb_path: str = "adb"):
        self.device  = device or config.ADB_DEVICE
        self.adb     = adb_path or config.ADB_PATH
        self._prefix = self._build_prefix()

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _build_prefix(self) -> list:
        prefix = [self.adb]
        if self.device:
            prefix += ["-s", self.device]
        return prefix

    def _run(self, *args, timeout: int = 15, check: bool = True) -> subprocess.CompletedProcess:
        cmd = self._prefix + list(args)
        log.debug("ADB: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            raise ADBError(
                f"'{self.adb}' not found. Install ADB and add it to PATH."
            )
        except subprocess.TimeoutExpired:
            raise ADBError(f"ADB command timed out: {' '.join(cmd)}")

        if check and result.returncode != 0:
            raise ADBError(
                f"ADB failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        return result

    # ─── Connection ──────────────────────────────────────────────────────────

    def devices(self) -> list[str]:
        """Return list of connected device serials."""
        result = self._run("devices", check=False)
        lines  = result.stdout.decode(errors="replace").strip().splitlines()
        return [
            l.split()[0]
            for l in lines[1:]  # skip header
            if l.strip() and "\t" in l and "offline" not in l
        ]

    def ensure_connected(self) -> str:
        """Auto-detect device if not set; raise if none found."""
        devs = self.devices()
        if not devs:
            raise ADBError(
                "No Android device/emulator connected. "
                "Connect via USB (enable USB Debugging) or start an emulator."
            )
        if self.device is None:
            self.device  = devs[0]
            self._prefix = self._build_prefix()
            log.info("Auto-selected device: %s", self.device)
        elif self.device not in devs:
            raise ADBError(f"Device '{self.device}' not found. Available: {devs}")
        return self.device

    def connect_tcp(self, ip: str, port: int = 5555) -> None:
        """Connect to a device over Wi-Fi ADB (Android 11+ supports wireless)."""
        self._run("connect", f"{ip}:{port}")
        self.device  = f"{ip}:{port}"
        self._prefix = self._build_prefix()
        log.info("Connected to %s:%d", ip, port)

    # ─── Screen capture ──────────────────────────────────────────────────────

    def screenshot(self) -> np.ndarray:
        """
        Capture the device screen and return it as an (H, W, 3) BGR numpy array
        (OpenCV-compatible).
        """
        result = self._run("exec-out", "screencap", "-p", timeout=30)
        img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
        arr = np.array(img)
        # PIL → RGB; convert to BGR for OpenCV
        return arr[:, :, ::-1].copy()

    def save_screenshot(self, path: str) -> None:
        """Capture and save screenshot to file."""
        frame = self.screenshot()
        import cv2
        cv2.imwrite(path, frame)
        log.debug("Screenshot saved: %s", path)

    # ─── Input actions ───────────────────────────────────────────────────────

    def tap(self, x: int, y: int, delay: float = None) -> None:
        """Tap at (x, y) in screen coordinates."""
        self._run("shell", "input", "tap", str(x), str(y))
        time.sleep(delay if delay is not None else config.TAP_DELAY)
        log.debug("Tap (%d, %d)", x, y)

    def swipe(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = None,
    ) -> None:
        """Swipe from (x1,y1) to (x2,y2)."""
        dur = duration_ms if duration_ms is not None else config.SWIPE_DURATION_MS
        self._run("shell", "input", "swipe",
                  str(x1), str(y1), str(x2), str(y2), str(dur))
        time.sleep(0.2)
        log.debug("Swipe (%d,%d)→(%d,%d) %dms", x1, y1, x2, y2, dur)

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        """Long-press at (x, y)."""
        self.swipe(x, y, x, y, duration_ms)

    def key_event(self, keycode: int) -> None:
        """Send an Android key event (see android.view.KeyEvent constants)."""
        self._run("shell", "input", "keyevent", str(keycode))
        time.sleep(0.1)

    def press_back(self)   -> None: self.key_event(4)
    def press_home(self)   -> None: self.key_event(3)
    def press_recent(self) -> None: self.key_event(187)
    def press_enter(self)  -> None: self.key_event(66)

    def type_text(self, text: str) -> None:
        """Type text (no special chars; use key_event for those)."""
        escaped = text.replace(" ", "%s").replace("'", "\\'")
        self._run("shell", "input", "text", escaped)
        time.sleep(0.2)

    # ─── App control ─────────────────────────────────────────────────────────

    def launch_app(self, package: str, activity: str = "") -> None:
        """Launch an app by package name (optionally specify activity)."""
        target = f"{package}/{activity}" if activity else package
        if activity:
            self._run("shell", "am", "start", "-n", target)
        else:
            self._run("shell", "monkey", "-p", package,
                      "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(2.0)
        log.info("Launched: %s", target)

    def kill_app(self, package: str) -> None:
        self._run("shell", "am", "force-stop", package)
        log.info("Killed: %s", package)

    def current_package(self) -> str:
        """Return the package name of the currently focused app."""
        res = self._run(
            "shell",
            "dumpsys", "window", "windows",
            "|", "grep", "-E", "mCurrentFocus",
            check=False,
        )
        out = res.stdout.decode(errors="replace")
        # mCurrentFocus=Window{... package/activity}
        import re
        m = re.search(r"mCurrentFocus=Window\{[^}]+\s+([\w.]+)/", out)
        return m.group(1) if m else ""

    def screen_on(self) -> None:
        """Turn on the screen if it's off."""
        self._run("shell", "input", "keyevent", "224")
        time.sleep(0.5)

    def unlock_screen(self, pin: str = "") -> None:
        """Wake + swipe-up to unlock; optionally enter PIN."""
        self.screen_on()
        self.swipe(540, 1800, 540, 900, 500)
        time.sleep(0.5)
        if pin:
            self.type_text(pin)
            self.press_enter()

    # ─── Utility ─────────────────────────────────────────────────────────────

    def get_screen_size(self) -> Tuple[int, int]:
        """Return (width, height) of the device screen."""
        res = self._run("shell", "wm", "size")
        out = res.stdout.decode(errors="replace").strip()
        # e.g. "Physical size: 1080x2316"
        import re
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return config.SCREEN_WIDTH, config.SCREEN_HEIGHT

    def shell(self, *cmd: str) -> str:
        """Run a raw adb shell command and return stdout."""
        result = self._run("shell", *cmd, check=False)
        return result.stdout.decode(errors="replace").strip()
