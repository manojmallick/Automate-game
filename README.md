# Android Game Automation Bot

> Out of the box the tap coordinates and states are tuned for a **knife-throw** game (tap to throw a knife at a rotating target) on a 1080×2316 screen. Other games work after calibration and a custom `_play_turn()` (see below).

Fully automates Android game-play via ADB (Android Debug Bridge) with:
- **Automatic ad skipping** — detects and dismisses interstitial, banner, and reward-video ads
- **Game-state machine** — classifies every screen and takes the right action
- **Template matching** — identify menus, level-complete screens, etc. from screenshots
- **OCR fallback** — reads on-screen text when templates aren't available
- **Calibration tool** — interactive GUI to record tap coordinates and capture templates
- **Live preview** — optional window showing the bot's current action

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | |
| ADB (`adb`) | Install [Android SDK Platform Tools](https://developer.android.com/studio/releases/platform-tools) |
| USB Debugging | Enable on the Android device (Settings → Developer Options → USB Debugging) |
| Tesseract OCR | Optional, improves ad detection: `sudo apt install tesseract-ocr` |

```bash
pip install -r requirements.txt
```

---

## Quick Start

### 1. Connect your phone

**USB:**
```bash
adb devices          # should list your device
```

**Wi-Fi (Android 11+):**
```bash
# On phone: Settings → Developer Options → Wireless Debugging → enable
# Then pair & connect, or use:
python main.py --ip 192.168.1.XX
```

---

### 2. Calibrate tap positions (one-time setup)

```bash
python calibrate.py --mode tap
```

A window opens showing your phone screen.
**Click on each button** in the order shown (Play, Next Level, Answer options, etc.).
Copy the printed coordinates into `config.py → GAME_TAPS`.

---

### 3. Capture game-state templates (one-time setup)

```bash
python calibrate.py --mode template
```

Follow the prompts — navigate to each game screen, then drag a rectangle around the unique UI region.
Templates are saved in `templates/`.

---

### 4. Run the bot

```bash
# Basic run (auto-detect device)
python main.py

# Launch the game automatically
python main.py --package com.your.game.package

# Wi-Fi connection + live preview
python main.py --ip 192.168.1.42 --preview

# Verbose logging + save to file
python main.py --verbose --log-file logs/bot.log

# Stop after 500 loops
python main.py --max-loops 500
```

Press **Ctrl+C** to stop gracefully.

---

## File Structure

```
automate-game/
├── main.py          ← Bot runner (entry point)
├── config.py        ← All settings: tap coords, thresholds, paths
├── adb_controller.py← ADB wrapper (tap, swipe, screenshot, …)
├── vision.py        ← Template matching, colour detection, OCR
├── ad_skipper.py    ← Ad detection & dismissal logic
├── game_states.py   ← State machine: detect screen → take action
├── calibrate.py     ← Interactive calibration tool
├── requirements.txt
├── templates/       ← Game-state template PNG images (you capture these)
└── debug_screenshots/← Auto-saved debug frames (when enabled in config)
```

---

## How It Works

Each loop (every `SCREENSHOT_INTERVAL` seconds, default 1.0) the bot:

1. Takes a screenshot over ADB.
2. Checks for an ad first (highest priority) and dismisses it if present.
3. Classifies the screen: template match first, OCR/colour heuristics as a fallback.
4. Runs the handler for that state.
5. If the same state repeats for `MAX_STUCK_LOOPS` loops, runs a recovery action.

### Game states

| State | Meaning | Action |
|---|---|---|
| `AD` | Ad overlay on screen | Run the ad-skipping routine |
| `LOADING` | Splash or loading screen | Wait 1.5 s |
| `MAIN_MENU` | Main menu | Tap `play_button` |
| `DAILY_REWARD` | Daily reward popup | Tap `collect_reward`, then `continue_after_win` |
| `LEVEL_SELECT` | Level picker | Tap `play_button` |
| `IN_GAME` | Playing | Throw a knife (`throw_knife` tap) |
| `LEVEL_COMPLETE` | Level cleared | Tap `next_level` / `continue_after_win` |
| `LEVEL_FAILED` | Failed level (fallback) | Tap `no_thanks` if OCR sees it, otherwise `restart` |
| `CONTINUE_PROMPT` | "CONTINUE?" countdown | Tap `no_thanks` |
| `GAME_OVER` | Post-death screen | Tap `restart` |
| `PAUSED` | Pause overlay | Press Back |
| `UNKNOWN` | Unrecognised screen | Tap the screen centre |

### Command-line options (`main.py`)

| Option | Description |
|---|---|
| `--device SERIAL` | ADB device serial (default: auto-detect) |
| `--ip ADDRESS` / `--port N` | Connect over Wi-Fi (default port 5555) |
| `--package NAME` | Launch the game by package name |
| `--max-loops N` | Stop after N loops (0 = run until Ctrl+C) |
| `--interval SECONDS` | Override the loop interval |
| `--preview` | Show a live preview window |
| `--verbose` | Debug logging |
| `--log-file PATH` | Also write logs to a file |

### Key settings (`config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `SCREEN_WIDTH`, `SCREEN_HEIGHT` | 1080, 2316 | Device resolution; all coordinates assume this |
| `MATCH_THRESHOLD` | 0.80 | Template-match strictness (lower = more permissive) |
| `AD_SKIP_WAIT_SECONDS` | 35 | Longest wait for a skip button on reward videos |
| `MAX_STUCK_LOOPS` | 20 | Loops in one state before recovery |
| `TAP_DELAY` | 0.3 | Pause after each tap, in seconds |
| `SAVE_DEBUG_SCREENSHOTS` | True | Save frames to `debug_screenshots/` |

---

## Customising for Your Game

### Tap coordinates (`config.py`)
```python
GAME_TAPS = {
    "play_button":     (540, 1800),   # ← (x, y) in pixels
    "answer_option_1": (200, 1200),
    # … add more as needed
}
```
Run `python calibrate.py --mode tap` to get the right values interactively.

### Game-play logic (`game_states.py`)

Override `_play_turn()` inside `GameStateMachine` for your specific game:

```python
def _play_turn(self, frame: np.ndarray) -> None:
    # Example: always tap option 1
    self.adb.tap(*config.GAME_TAPS["answer_option_1"])
```

### Ad close-button regions (`config.py`)

If ads appear in unusual positions, add their hotspot regions:
```python
AD_CLOSE_REGIONS = [
    (x, y, width, height),   # add more regions here
]
```

---

## Ad Skipping Details

The bot uses a layered approach to dismiss ads:

1. **Template match** — compares against `templates/ad_overlay.png`
2. **Colour detection** — scans hotspot regions for white/red "×" pixels
3. **OCR text** — looks for "Skip", "Close", "×", "Got it" etc.
4. **Fallback taps** — tries all known close-button coordinates

For reward-video ads, `skip_reward_video()` polls every second until the skip button appears (up to 35 s by default).

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `'adb' not found` | Add ADB to PATH or set `ADB_PATH` in `config.py` |
| `No device connected` | Check USB cable, enable USB Debugging, run `adb devices` |
| Bot stuck in unknown state | Run calibration to capture templates |
| Templates not matching | Lower `MATCH_THRESHOLD` in `config.py` (try 0.70) |
| Ads not detected | Add more hotspot regions to `AD_CLOSE_REGIONS` |
| OCR not working | Install Tesseract: `sudo apt install tesseract-ocr` |

---

## Notes

- This tool is intended for **personal, educational, and learning purposes**.
- Do not use it to violate a game's Terms of Service.
- Tested on 1080×2316 screens; adjust `SCREEN_WIDTH`/`SCREEN_HEIGHT` in `config.py` for other resolutions.
