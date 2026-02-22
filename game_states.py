"""
Game State Machine
Defines every recognisable game screen and the actions to take in each state.
Extend or override states to match your specific game's UI.
"""

import logging
import random
import time
from enum import Enum, auto
from typing import Optional

import numpy as np

import config
import vision
from adb_controller import ADBController
from ad_skipper import AdSkipper

log = logging.getLogger(__name__)


class State(Enum):
    UNKNOWN        = auto()
    AD             = auto()
    LOADING        = auto()
    MAIN_MENU      = auto()
    DAILY_REWARD   = auto()
    LEVEL_SELECT   = auto()
    IN_GAME        = auto()
    LEVEL_COMPLETE = auto()
    LEVEL_FAILED   = auto()
    PAUSED         = auto()


class GameStateMachine:
    """
    Detects the current game screen from a screenshot and performs the
    appropriate automated action.
    """

    def __init__(self, adb: ADBController, ad_skipper: AdSkipper):
        self.adb        = adb
        self.ad_skipper = ad_skipper

        self._prev_state: State = State.UNKNOWN
        self._stuck_count: int  = 0
        self._levels_played: int = 0
        self._last_frame: Optional[np.ndarray] = None

    # ─── State detection ─────────────────────────────────────────────────────

    def detect_state(self, frame: np.ndarray) -> State:
        """
        Classify the current screen into a State enum value.
        Priority order matters — check ad first, then loading, then game states.
        """
        # 1. Ad overlay (highest priority)
        if self.ad_skipper.is_ad_showing(frame):
            return State.AD

        # 2. Template-based detection
        checks = [
            (State.LOADING,        config.TEMPLATES.get("loading")),
            (State.DAILY_REWARD,   config.TEMPLATES.get("daily_reward")),
            (State.LEVEL_COMPLETE, config.TEMPLATES.get("level_complete")),
            (State.LEVEL_FAILED,   config.TEMPLATES.get("level_failed")),
            (State.MAIN_MENU,      config.TEMPLATES.get("main_menu")),
            (State.LEVEL_SELECT,   config.TEMPLATES.get("level_select")),
            (State.IN_GAME,        config.TEMPLATES.get("in_game")),
        ]

        for state, tpl_path in checks:
            if tpl_path and vision.match_template(frame, tpl_path):
                return state

        # 3. Colour / structural heuristics (fallback when templates missing)
        state = self._heuristic_detect(frame)
        return state

    def _heuristic_detect(self, frame: np.ndarray) -> State:
        """
        Lightweight heuristic detection used when template images aren't
        available yet.  Looks at average colours, brightness patterns, etc.
        """
        h, w = frame.shape[:2]

        # Very bright frame → probably loading / white splash
        mean_brightness = frame.mean()
        if mean_brightness > 230:
            return State.LOADING

        # Dark overlay across full screen → ad or pause
        if mean_brightness < 30:
            return State.AD

        # OCR-based fallback for common UI labels
        top_half   = frame[:h // 2, :]
        bottom_half = frame[h // 2:, :]

        top_text    = vision.ocr_region(top_half)
        bottom_text = vision.ocr_region(bottom_half)
        all_text    = top_text + " " + bottom_text

        if any(k in all_text for k in ["level complete", "well done", "you win", "stage clear"]):
            return State.LEVEL_COMPLETE
        if any(k in all_text for k in ["game over", "failed", "try again", "level failed"]):
            return State.LEVEL_FAILED
        if any(k in all_text for k in ["daily reward", "free gift", "spin"]):
            return State.DAILY_REWARD
        if any(k in all_text for k in ["play", "start", "tap to play", "begin"]):
            return State.MAIN_MENU
        if any(k in all_text for k in ["select level", "chapter", "world"]):
            return State.LEVEL_SELECT
        if any(k in all_text for k in ["pause", "menu", "home"]):
            return State.PAUSED

        return State.IN_GAME  # assume in-game if nothing else matched

    # ─── State handlers ──────────────────────────────────────────────────────

    def handle(self, frame: np.ndarray) -> State:
        """
        Detect state and execute corresponding action.
        Returns the detected state for logging.
        """
        state = self.detect_state(frame)

        if state == self._prev_state:
            self._stuck_count += 1
        else:
            self._stuck_count  = 0
            self._prev_state   = state

        log.info("[State] %s  (stuck=%d)", state.name, self._stuck_count)

        # Safety: break out of infinite loops
        if self._stuck_count >= config.MAX_STUCK_LOOPS:
            log.warning("Stuck in state %s for %d loops — applying recovery",
                        state.name, self._stuck_count)
            self._recover(state, frame)
            self._stuck_count = 0
            return state

        # Dispatch
        handlers = {
            State.AD:             self._handle_ad,
            State.LOADING:        self._handle_loading,
            State.MAIN_MENU:      self._handle_main_menu,
            State.DAILY_REWARD:   self._handle_daily_reward,
            State.LEVEL_SELECT:   self._handle_level_select,
            State.IN_GAME:        self._handle_in_game,
            State.LEVEL_COMPLETE: self._handle_level_complete,
            State.LEVEL_FAILED:   self._handle_level_failed,
            State.PAUSED:         self._handle_paused,
            State.UNKNOWN:        self._handle_unknown,
        }
        handlers.get(state, self._handle_unknown)(frame)
        return state

    # ─── Individual handlers ─────────────────────────────────────────────────

    def _handle_ad(self, frame: np.ndarray) -> None:
        self.ad_skipper.dismiss_ad(frame)
        time.sleep(1.0)

    def _handle_loading(self, frame: np.ndarray) -> None:
        log.debug("Waiting for loading screen to pass…")
        time.sleep(1.5)

    def _handle_main_menu(self, frame: np.ndarray) -> None:
        log.info("Main menu — pressing Play")
        self.adb.tap(*config.GAME_TAPS["play_button"])
        time.sleep(1.0)

    def _handle_daily_reward(self, frame: np.ndarray) -> None:
        log.info("Collecting daily reward")
        self.adb.tap(*config.GAME_TAPS["collect_reward"])
        time.sleep(1.0)
        # Dismiss any follow-up popup
        self.adb.tap(*config.GAME_TAPS["continue_after_win"])
        time.sleep(0.5)

    def _handle_level_select(self, frame: np.ndarray) -> None:
        log.info("Level select — tapping first available level")
        # Try to find and tap the first unfinished level via template
        # Fallback: tap the play button
        self.adb.tap(*config.GAME_TAPS["play_button"])
        time.sleep(1.0)

    def _handle_in_game(self, frame: np.ndarray) -> None:
        """
        Core game-play logic.  This is the part you need to customise
        for your specific game.

        The default implementation:
          • Detects 4 answer options and taps them in sequence
          • Falls back to tapping random answer regions
        """
        log.debug("In-game — executing play action")
        self._play_turn(frame)

    def _handle_level_complete(self, frame: np.ndarray) -> None:
        self._levels_played += 1
        log.info("Level complete! (total=%d) — advancing", self._levels_played)
        self.adb.tap(*config.GAME_TAPS["next_level"])
        time.sleep(1.2)
        # Sometimes a second tap is needed to confirm
        self.adb.tap(*config.GAME_TAPS["continue_after_win"])
        time.sleep(0.5)

    def _handle_level_failed(self, frame: np.ndarray) -> None:
        log.info("Level failed — retrying")
        self.adb.tap(*config.GAME_TAPS["retry"])
        time.sleep(1.2)

    def _handle_paused(self, frame: np.ndarray) -> None:
        log.info("Game paused — pressing Back to resume")
        self.adb.press_back()
        time.sleep(0.5)

    def _handle_unknown(self, frame: np.ndarray) -> None:
        log.warning("Unknown state — tapping screen centre to progress")
        self.adb.tap(config.SCREEN_WIDTH // 2, config.SCREEN_HEIGHT // 2)
        time.sleep(1.0)

    # ─── Game-play logic ─────────────────────────────────────────────────────

    def _play_turn(self, frame: np.ndarray) -> None:
        """
        Default turn logic: try options in order, or tap random option.
        Override this method for game-specific logic.

        For quiz/word games like in the screenshots:
        - Option 1: top-left answer
        - Option 2: top-right answer
        - Option 3: bottom-left answer
        - Option 4: bottom-right answer
        """
        options = [
            config.GAME_TAPS["answer_option_1"],
            config.GAME_TAPS["answer_option_2"],
            config.GAME_TAPS["answer_option_3"],
            config.GAME_TAPS["answer_option_4"],
        ]

        # If there are template images for correct answers, match first
        # Otherwise cycle through options (useful for elimination-style games)
        chosen = self._smart_pick(frame, options)
        self.adb.tap(*chosen)
        time.sleep(0.6)

    def _smart_pick(self, frame, options):
        """
        Try to pick the best answer.
        • If templates for options exist, use them.
        • Otherwise rotate through options across turns.
        """
        # Rotate through options each call for variety
        idx = self._levels_played % len(options)
        return options[idx]

    # ─── Recovery ────────────────────────────────────────────────────────────

    def _recover(self, stuck_state: State, frame: np.ndarray) -> None:
        """Take a drastic action to break out of a stuck state."""
        log.warning("Recovery from stuck state: %s", stuck_state.name)

        if stuck_state == State.AD:
            # Try pressing Back key
            self.adb.press_back()
            time.sleep(1.0)
            # Try all fallback positions
            for tx, ty in [(960, 80), (120, 80), (540, 2150)]:
                self.adb.tap(tx, ty, delay=0.5)
        elif stuck_state in (State.LOADING, State.UNKNOWN):
            # Restart the app
            time.sleep(3.0)
            self.adb.press_back()
            time.sleep(1.0)
        else:
            # Generic: tap centre
            self.adb.tap(config.SCREEN_WIDTH // 2, config.SCREEN_HEIGHT // 2)
            time.sleep(1.0)

    # ─── Stats ────────────────────────────────────────────────────────────────

    @property
    def levels_played(self) -> int:
        return self._levels_played
