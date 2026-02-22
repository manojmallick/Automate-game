#!/usr/bin/env python3
"""
Android Game Automation Bot — Main Entry Point
===============================================
Continuously screenshots the device, classifies the game screen,
and performs the correct action automatically (including skipping ads).

Usage:
    python main.py [options]

Examples:
    # Run with USB-connected device, auto-detect
    python main.py

    # Connect to a specific device/emulator
    python main.py --device emulator-5554

    # Connect via Wi-Fi ADB (Android 11+ wireless debugging)
    python main.py --ip 192.168.1.42

    # Limit to 200 iterations then stop
    python main.py --max-loops 200

    # Launch the game package first
    python main.py --package com.example.mygame

    # Show a live preview window (requires display)
    python main.py --preview
"""

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from datetime import datetime

import cv2

import config
from adb_controller import ADBController, ADBError
from ad_skipper import AdSkipper
from game_states import GameStateMachine, State
import vision


# ─── Logging ──────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False, log_file: str = None):
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


# ─── Stats display ────────────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self.start_time    = time.time()
        self.loop_count    = 0
        self.state_counts  = {}
        self.errors        = 0

    def record(self, state: State):
        self.loop_count += 1
        name = state.name
        self.state_counts[name] = self.state_counts.get(name, 0) + 1

    def elapsed(self) -> str:
        s = int(time.time() - self.start_time)
        return f"{s // 3600:02d}h{(s % 3600) // 60:02d}m{s % 60:02d}s"

    def summary(self, levels: int, skips: int) -> str:
        lines = [
            "─" * 50,
            f"  Elapsed      : {self.elapsed()}",
            f"  Loops        : {self.loop_count}",
            f"  Levels played: {levels}",
            f"  Ads skipped  : {skips}",
            f"  Errors       : {self.errors}",
            "  State breakdown:",
        ]
        for name, count in sorted(self.state_counts.items(),
                                   key=lambda kv: -kv[1]):
            lines.append(f"    {name:<20} {count}")
        lines.append("─" * 50)
        return "\n".join(lines)


# ─── Bot ──────────────────────────────────────────────────────────────────────

class GameBot:
    def __init__(self, args: argparse.Namespace):
        self.args  = args
        self.adb   = ADBController(device=args.device)
        self.ad_sk = AdSkipper(self.adb)
        self.gsm   = GameStateMachine(self.adb, self.ad_sk)
        self.stats = Stats()
        self._running = True

        # Handle Ctrl+C gracefully
        signal.signal(signal.SIGINT,  self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, signum, frame):
        logging.getLogger(__name__).info("Shutdown signal received")
        self._running = False

    def setup(self):
        log = logging.getLogger(__name__)

        # Connect
        if self.args.ip:
            self.adb.connect_tcp(self.args.ip, self.args.port)
        else:
            device = self.adb.ensure_connected()
            log.info("Device: %s", device)

        # Detect and log screen size
        w, h = self.adb.get_screen_size()
        log.info("Screen: %dx%d", w, h)

        # Wake screen
        self.adb.screen_on()

        # Launch game if package specified
        if self.args.package:
            log.info("Launching: %s", self.args.package)
            self.adb.launch_app(self.args.package)
            time.sleep(3.0)

        # Create template dir
        Path("templates").mkdir(exist_ok=True)
        if config.SAVE_DEBUG_SCREENSHOTS:
            Path(config.DEBUG_DIR).mkdir(exist_ok=True)

    def run(self):
        log = logging.getLogger(__name__)
        log.info("Bot started.  Press Ctrl+C to stop.")

        loop = 0
        while self._running:
            # Loop limit
            if self.args.max_loops and loop >= self.args.max_loops:
                log.info("Reached max loops (%d). Stopping.", self.args.max_loops)
                break

            loop += 1
            t0 = time.time()

            try:
                frame = self.adb.screenshot()
            except ADBError as e:
                log.error("Screenshot failed: %s", e)
                self.stats.errors += 1
                time.sleep(2.0)
                continue

            # Optional: save debug screenshot every N loops
            if config.SAVE_DEBUG_SCREENSHOTS and loop % 30 == 0:
                vision.save_debug(frame, f"loop_{loop:05d}")

            # Run the state machine
            try:
                state = self.gsm.handle(frame)
                self.stats.record(state)
            except Exception as e:
                log.exception("State machine error: %s", e)
                self.stats.errors += 1

            # Optional live preview
            if self.args.preview:
                preview = vision.draw_debug_overlay(
                    frame,
                    f"[{loop}] {state.name}  ads={self.ad_sk.total_skips}  "
                    f"lvls={self.gsm.levels_played}",
                )
                preview = cv2.resize(preview, (540, 1158))
                cv2.imshow("Bot Preview (Q to quit)", preview)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            # Throttle loop
            elapsed = time.time() - t0
            sleep   = max(0.0, config.SCREENSHOT_INTERVAL - elapsed)
            if sleep > 0:
                time.sleep(sleep)

            # Periodic status
            if loop % 20 == 0:
                log.info(
                    "Loop %d | state=%s | levels=%d | ads=%d | errors=%d",
                    loop,
                    state.name,
                    self.gsm.levels_played,
                    self.ad_sk.total_skips,
                    self.stats.errors,
                )

        if self.args.preview:
            cv2.destroyAllWindows()

        print(self.stats.summary(self.gsm.levels_played, self.ad_sk.total_skips))


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Android Game Automation Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--device",    default=None,
                   help="ADB device serial (default: auto-detect)")
    p.add_argument("--ip",        default=None,
                   help="Device IP for wireless ADB")
    p.add_argument("--port",      type=int, default=5555,
                   help="Wireless ADB port (default: 5555)")
    p.add_argument("--package",   default=None,
                   help="Game package name to launch (e.g. com.example.game)")
    p.add_argument("--max-loops", type=int, default=0,
                   help="Stop after N loops (0 = run forever)")
    p.add_argument("--interval",  type=float, default=None,
                   help="Override SCREENSHOT_INTERVAL from config")
    p.add_argument("--preview",   action="store_true",
                   help="Show live preview window (requires display)")
    p.add_argument("--verbose",   action="store_true",
                   help="Enable DEBUG logging")
    p.add_argument("--log-file",  default=None,
                   help="Write logs to this file")
    return p.parse_args()


def main():
    args = parse_args()
    setup_logging(args.verbose, args.log_file)
    log = logging.getLogger(__name__)

    if args.interval:
        config.SCREENSHOT_INTERVAL = args.interval

    bot = GameBot(args)
    try:
        bot.setup()
    except ADBError as e:
        log.error("Setup failed: %s", e)
        sys.exit(1)

    bot.run()
    log.info("Bot stopped.")


if __name__ == "__main__":
    main()
