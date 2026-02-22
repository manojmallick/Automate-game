"""
Game State Machine
Defines every recognisable game screen and the actions to take in each state.

Knife-throw game flow
─────────────────────
  IN_GAME          → tap to throw knives at the rotating target
    ↓ (hit sword)
  CONTINUE_PROMPT  → click "NO, THANKS"
    ↓
  GAME_OVER        → click "RESTART"
    ↓
  IN_GAME          (loop)

  AD               → wait for skip/close button, click it → back to IN_GAME
"""

import logging
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
    UNKNOWN         = auto()
    AD              = auto()
    LOADING         = auto()
    MAIN_MENU       = auto()
    DAILY_REWARD    = auto()
    LEVEL_SELECT    = auto()
    IN_GAME         = auto()
    LEVEL_COMPLETE  = auto()
    LEVEL_FAILED    = auto()
    PAUSED          = auto()
    CONTINUE_PROMPT = auto()   # "CONTINUE?" countdown + "NO, THANKS" button
    GAME_OVER       = auto()   # post-death screen with "RESTART" button


class GameStateMachine:
    """
    Detects the current game screen from a screenshot and performs the
    appropriate automated action.
    """

    def __init__(self, adb: ADBController, ad_skipper: AdSkipper):
        self.adb        = adb
        self.ad_skipper = ad_skipper

        self._prev_state:    State              = State.UNKNOWN
        self._stuck_count:   int                = 0
        self._levels_played: int                = 0
        self._last_frame:    Optional[np.ndarray] = None

    # ─── State detection ─────────────────────────────────────────────────────

    def detect_state(self, frame: np.ndarray) -> State:
        """
        Classify the current screen into a State enum value.
        Priority order matters — check ad first, then loading, then game states.
        """
        # 1. Ad overlay (highest priority — must not miss)
        if self.ad_skipper.is_ad_showing(frame):
            return State.AD

        # 2. Template-based detection (fast, exact)
        template_checks = [
            (State.LOADING,         config.TEMPLATES.get("loading")),
            (State.DAILY_REWARD,    config.TEMPLATES.get("daily_reward")),
            (State.CONTINUE_PROMPT, config.TEMPLATES.get("continue_prompt")),
            (State.GAME_OVER,       config.TEMPLATES.get("game_over")),
            (State.LEVEL_COMPLETE,  config.TEMPLATES.get("level_complete")),
            (State.LEVEL_FAILED,    config.TEMPLATES.get("level_failed")),
            (State.MAIN_MENU,       config.TEMPLATES.get("main_menu")),
            (State.LEVEL_SELECT,    config.TEMPLATES.get("level_select")),
            (State.IN_GAME,         config.TEMPLATES.get("in_game")),
        ]
        for state, tpl_path in template_checks:
            if tpl_path and vision.match_template(frame, tpl_path):
                return state

        # 3. OCR / colour heuristics (fallback when templates not yet saved)
        return self._heuristic_detect(frame)

    def _heuristic_detect(self, frame: np.ndarray) -> State:
        """
        Lightweight heuristic detection used when template images are not
        available yet.  Reads text via OCR and checks brightness patterns.
        """
        h, w = frame.shape[:2]

        mean_brightness = float(frame.mean())
        # Very bright splash → loading
        if mean_brightness > 230:
            return State.LOADING
        # Near-black full screen → possible ad / pause overlay
        if mean_brightness < 15:
            return State.AD

        # OCR on top and bottom halves
        top_text    = vision.ocr_region(frame, (0, 0, w, h // 2))
        bottom_text = vision.ocr_region(frame, (0, h // 2, w, h // 2))
        all_text    = (top_text + " " + bottom_text).lower()

        # ── Knife-throw specific screens ─────────────────────────────────────
        # "CONTINUE?" screen: has the word "continue" + "no" + "thanks"
        if "no, thanks" in all_text or ("continue" in all_text and "no" in all_text):
            return State.CONTINUE_PROMPT

        # Restart / game-over screen
        if "restart" in all_text:
            return State.GAME_OVER

        # ── Generic game screens ──────────────────────────────────────────────
        if any(k in all_text for k in ["level complete", "well done", "you win",
                                        "stage clear", "boss defeated"]):
            return State.LEVEL_COMPLETE

        if any(k in all_text for k in ["game over", "failed", "try again",
                                        "level failed"]):
            return State.LEVEL_FAILED

        if any(k in all_text for k in ["daily reward", "free gift", "spin"]):
            return State.DAILY_REWARD

        if any(k in all_text for k in ["tap to play", "tap to start",
                                        "tap to begin", "start game"]):
            return State.MAIN_MENU

        if any(k in all_text for k in ["select level", "chapter", "world"]):
            return State.LEVEL_SELECT

        if any(k in all_text for k in ["paused", "pause menu"]):
            return State.PAUSED

        # Default: assume we are in-game
        return State.IN_GAME

    # ─── Main dispatch ───────────────────────────────────────────────────────

    def handle(self, frame: np.ndarray) -> State:
        """
        Detect state and execute corresponding action.
        Returns the detected state for logging.
        """
        state = self.detect_state(frame)

        if state == self._prev_state:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
            self._prev_state  = state

        log.info("[State] %s  (stuck=%d)", state.name, self._stuck_count)

        if self._stuck_count >= config.MAX_STUCK_LOOPS:
            log.warning("Stuck in %s for %d loops — applying recovery",
                        state.name, self._stuck_count)
            self._recover(state, frame)
            self._stuck_count = 0
            return state

        handlers = {
            State.AD:             self._handle_ad,
            State.LOADING:        self._handle_loading,
            State.MAIN_MENU:      self._handle_main_menu,
            State.DAILY_REWARD:   self._handle_daily_reward,
            State.LEVEL_SELECT:   self._handle_level_select,
            State.IN_GAME:        self._handle_in_game,
            State.LEVEL_COMPLETE: self._handle_level_complete,
            State.LEVEL_FAILED:   self._handle_level_failed,
            State.CONTINUE_PROMPT: self._handle_continue_prompt,
            State.GAME_OVER:      self._handle_game_over,
            State.PAUSED:         self._handle_paused,
            State.UNKNOWN:        self._handle_unknown,
        }
        handlers.get(state, self._handle_unknown)(frame)
        return state

    # ─── Individual handlers ─────────────────────────────────────────────────

    def _handle_ad(self, frame: np.ndarray) -> None:
        """Wait for the skip/close button then tap it."""
        self.ad_skipper.dismiss_ad(frame)

    def _handle_loading(self, frame: np.ndarray) -> None:
        log.debug("Loading — waiting…")
        time.sleep(1.5)

    def _handle_main_menu(self, frame: np.ndarray) -> None:
        log.info("Main menu — pressing Play")
        self.adb.tap(*config.GAME_TAPS["play_button"])
        time.sleep(1.0)

    def _handle_daily_reward(self, frame: np.ndarray) -> None:
        log.info("Collecting daily reward")
        self.adb.tap(*config.GAME_TAPS["collect_reward"])
        time.sleep(1.0)
        self.adb.tap(*config.GAME_TAPS["continue_after_win"])
        time.sleep(0.5)

    def _handle_level_select(self, frame: np.ndarray) -> None:
        log.info("Level select — tapping Play")
        self.adb.tap(*config.GAME_TAPS["play_button"])
        time.sleep(1.0)

    def _handle_in_game(self, frame: np.ndarray) -> None:
        """Throw a knife at the rotating target."""
        log.debug("In-game — throwing knife")
        self._throw_knife(frame)

    def _handle_level_complete(self, frame: np.ndarray) -> None:
        self._levels_played += 1
        log.info("Level complete! (total=%d) — advancing", self._levels_played)
        self.adb.tap(*config.GAME_TAPS["next_level"])
        time.sleep(1.2)
        self.adb.tap(*config.GAME_TAPS["continue_after_win"])
        time.sleep(0.5)

    def _handle_level_failed(self, frame: np.ndarray) -> None:
        """Generic level-failed fallback (should normally hit CONTINUE_PROMPT first)."""
        log.info("Level failed — looking for NO, THANKS or RESTART")
        # Try "NO, THANKS" first; if not visible, go straight to RESTART
        text = vision.ocr_region(frame).lower()
        if "no" in text and "thanks" in text:
            self.adb.tap(*config.GAME_TAPS["no_thanks"])
        else:
            self.adb.tap(*config.GAME_TAPS["restart"])
        time.sleep(1.5)

    def _handle_continue_prompt(self, frame: np.ndarray) -> None:
        """
        'CONTINUE?' screen with a countdown timer.
        Click 'NO, THANKS' to skip spending gems and go to the restart screen.
        """
        log.info("Continue prompt — clicking NO, THANKS")
        self.adb.tap(*config.GAME_TAPS["no_thanks"])
        time.sleep(1.5)

    def _handle_game_over(self, frame: np.ndarray) -> None:
        """
        Post-death game-over / restart screen.
        Click 'RESTART' to play the level again from the beginning.
        """
        log.info("Game over — clicking RESTART")
        self.adb.tap(*config.GAME_TAPS["restart"])
        time.sleep(1.8)

    def _handle_paused(self, frame: np.ndarray) -> None:
        log.info("Paused — pressing Back to resume")
        self.adb.press_back()
        time.sleep(0.5)

    def _handle_unknown(self, frame: np.ndarray) -> None:
        log.warning("Unknown state — tapping screen centre")
        self.adb.tap(config.SCREEN_WIDTH // 2, config.SCREEN_HEIGHT // 2)
        time.sleep(1.0)

    # ─── Knife-throw game-play logic ─────────────────────────────────────────

    def _throw_knife(self, frame: np.ndarray) -> None:
        """
        Tap the lower play-area to launch a knife/sword at the rotating target.
        A single tap per call keeps the timing clean; the main loop provides
        the repeat frequency via SCREENSHOT_INTERVAL.
        """
        self.adb.tap(*config.GAME_TAPS["throw_knife"])
        # Short settle time — the bot loop will screenshot again quickly
        time.sleep(0.25)

    # ─── Recovery ────────────────────────────────────────────────────────────

    def _recover(self, stuck_state: State, frame: np.ndarray) -> None:
        """Take a drastic action to break out of a stuck state."""
        log.warning("Recovery from stuck state: %s", stuck_state.name)

        if stuck_state == State.AD:
            self.adb.press_back()
            time.sleep(1.0)
            for tx, ty in [(1020, 80), (60, 80), (540, 2150)]:
                self.adb.tap(tx, ty, delay=0.5)

        elif stuck_state == State.CONTINUE_PROMPT:
            # Force-tap NO, THANKS a couple of times
            for _ in range(3):
                self.adb.tap(*config.GAME_TAPS["no_thanks"])
                time.sleep(0.8)

        elif stuck_state == State.GAME_OVER:
            for _ in range(3):
                self.adb.tap(*config.GAME_TAPS["restart"])
                time.sleep(1.0)

        elif stuck_state in (State.LOADING, State.UNKNOWN):
            time.sleep(3.0)
            self.adb.press_back()
            time.sleep(1.0)

        else:
            self.adb.tap(config.SCREEN_WIDTH // 2, config.SCREEN_HEIGHT // 2)
            time.sleep(1.0)

    # ─── Stats ───────────────────────────────────────────────────────────────

    @property
    def levels_played(self) -> int:
        return self._levels_played
