"""
Configuration for Android Game Automation Bot
Adjust these values to match your specific game and device.
"""

# ─── ADB Connection ──────────────────────────────────────────────────────────
ADB_DEVICE = None          # None = auto-detect; set to "emulator-5554" etc.
ADB_PATH   = "adb"         # path to adb binary (add to PATH or specify full path)

# ─── Device Screen ───────────────────────────────────────────────────────────
SCREEN_WIDTH  = 1080
SCREEN_HEIGHT = 2316

# ─── Screenshot Loop ─────────────────────────────────────────────────────────
SCREENSHOT_INTERVAL = 1.0   # seconds between each state-check loop iteration
SAVE_DEBUG_SCREENSHOTS = True
DEBUG_DIR = "debug_screenshots"

# ─── Game State Detection (template-match thresholds) ────────────────────────
MATCH_THRESHOLD = 0.80      # 0-1; lower = more permissive matching

# ─── Ad Detection ────────────────────────────────────────────────────────────
# Regions (x, y, w, h) to scan for common ad-close / skip buttons.
# Ordered by likelihood: top-right is most common for interstitials.
AD_CLOSE_REGIONS = [
    # Top-right corner (most common: interstitials, rewarded videos)
    (860,  30,  220, 220),
    # Top-left corner
    (0,    30,  220, 220),
    # Top-centre (some SDKs put close here)
    (380,  30,  320, 180),
    # Bottom-centre "Skip Ad" bar
    (270, 2050, 540, 200),
    # Bottom-right
    (840, 2050, 240, 200),
    # Mid-right (some reward-video SDKs)
    (860,  860, 220, 220),
]

# Text patterns (lower-case) that indicate an ad close button
AD_TEXT_PATTERNS = [
    "skip", "skip ad", "close", "×", "x", "got it",
    "no thanks", "dismiss", "continue", "resume",
]

# Colour thresholds for detecting the "×" close icon (BGR)
AD_CLOSE_BUTTON_COLORS = [
    # White-ish "×" on dark background
    {"lower": (200, 200, 200), "upper": (255, 255, 255)},
    # Red-ish close button
    {"lower": (0, 0, 150),     "upper": (80, 80, 255)},
]

# Max seconds to wait for an ad skip/close button to appear
AD_SKIP_WAIT_SECONDS = 35

# ─── Game-Specific Config ─────────────────────────────────────────────────────
# Template image paths (put your screenshots of game states in templates/)
TEMPLATES = {
    "main_menu":        "templates/main_menu.png",
    "level_select":     "templates/level_select.png",
    "in_game":          "templates/in_game.png",
    "level_complete":   "templates/level_complete.png",
    "level_failed":     "templates/level_failed.png",
    "ad_overlay":       "templates/ad_overlay.png",
    "loading":          "templates/loading.png",
    "daily_reward":     "templates/daily_reward.png",
    "continue_prompt":  "templates/continue_prompt.png",
    "game_over":        "templates/game_over.png",
}

# Tap coordinates for game actions (x, y) on a 1080×2316 screen.
# ── Knife-throw game ──────────────────────────────────────────────────────────
GAME_TAPS = {
    # Throw a knife at the rotating target (tap anywhere in the lower play area)
    "throw_knife":        (540, 1950),

    # Navigation
    "play_button":        (540, 1800),
    "next_level":         (540, 1900),
    "collect_reward":     (540, 1700),
    "continue_after_win": (540, 2000),

    # Post-death flow
    # "CONTINUE?" screen — "NO, THANKS" button (bottom-centre)
    "no_thanks":          (540, 2100),
    # Game-over / restart screen — "RESTART" button (centre)
    "restart":            (540, 1580),
}

# ─── Swipe Gestures ──────────────────────────────────────────────────────────
SWIPE_DURATION_MS = 300   # milliseconds

# ─── Retry / Safety ──────────────────────────────────────────────────────────
MAX_STUCK_LOOPS = 20       # restart action after this many loops with no change
TAP_DELAY       = 0.3      # seconds to wait after each tap
