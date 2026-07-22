# GEMINI.md

This file provides guidance to Google Gemini / Antigravity when working with code in this repository.

## What This Is

**MyWhisper** (brand: **Matan Digital**) is a Windows-only, fully offline Hebrew dictation desktop app. The user presses a global hotkey anywhere in the OS, speaks Hebrew, and the speech is transcribed **locally on the GPU** via `faster-whisper` (with full punctuation) and auto-pasted into the focused text field. No internet, no API costs, no cloud dependencies.

- **Language**: Python 3.12 (the ML stack lacks 3.14 wheels)
- **UI Framework**: PySide6 (Qt) — chosen over Tkinter for proper RTL/bidi Hebrew rendering
- **ML Engine**: `faster-whisper` (CTranslate2 backend) with `ivrit-ai/whisper-large-v3-turbo-ct2` Hebrew model
- **GPU**: NVIDIA CUDA (float16), with automatic CPU fallback (int8, greedy)
- **Target OS**: Windows 10/11
- **Current Version**: 1.10.0 (`app/version.py`)

---

## Commands

All Python runs through the isolated venv (`.venv`, Python 3.12). Run from the project root.

```powershell
# One-time setup: creates .venv (Python 3.12) and installs deps incl. CUDA libs
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# Sanity check: records 4s from the mic and transcribes (verifies GPU + Hebrew)
# First run downloads the Whisper model (~1.5–3 GB)
.\.venv\Scripts\python app\check_gpu.py

# Run the app (with console)
.\.venv\Scripts\python app\main.py

# Run silently to tray (no console window)
wscript scripts\run_mywishper.vbs

# Install/remove Windows autostart (creates MyWhisper.lnk in Startup folder)
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1

# Unit tests (corrections + history layers)
.\.venv\Scripts\python -m unittest discover tests

# In-place update (triggered from UI or manually)
powershell -ExecutionPolicy Bypass -File scripts\update.ps1
```

No linter is configured. Runtime logs go to `mywhisper.log` (UTF-8, rotating 1MB × 2 backups) via `app/applog.py`.

---

## Complete File Map

### Core Application (`app/`)

| File | Purpose |
|------|---------|
| `main.py` | **Orchestrator & entry point.** `Mywishper` class wires all components and runs a toggle-based state machine (idle → recording → transcribing → idle) driven by the global hotkey. Contains single-instance mutex, background model loading, idle/fullscreen resource polling, update checker, and the Qt event loop. |
| `ui.py` | **Full PySide6 GUI.** `MainWindow(FramelessWindow)` = title bar + nav rail + `QStackedWidget` with 3 pages: היסטוריה (history with search + per-card copy/delete), מילון (learned corrections dictionary), הגדרות (settings: theme, sound, hotkey, mic). Also: `Overlay` (floating recording HUD with animated wave bars), `CorrectionDialog`, `ChangelogDialog`, `HotkeyEdit` (key capture widget), `HistoryCard`. `AppUI(QObject)` is the thread-safe controller using signals. |
| `transcriber.py` | **Whisper wrapper.** `Transcriber` class with lazy `load()`/`unload()` lifecycle, `_add_cuda_dll_dirs()` for CUDA DLL resolution, and `transcribe(audio, hotwords)`. Auto-falls back from CUDA float16 to CPU int8. |
| `recorder.py` | **Audio capture.** `Recorder` class using `sounddevice` to capture 16kHz mono float32 via callback into a thread queue. `MicMonitor` for real-time volume meter in settings. `list_input_devices()` filters out virtual/loopback devices. |
| `hotkey.py` | **Global hotkeys.** Uses native Win32 `RegisterHotKey`/`UnregisterHotKey` via `ctypes`. `_HotkeyFilter(QAbstractNativeEventFilter)` intercepts `WM_HOTKEY` messages on the Qt thread. `HotkeyManager` for the main dictation key, `TempHotkey` for Esc during recording. |
| `corrections.py` | **Self-improving correction layer.** Manages `corrections.json` (wrong→right mappings) and `dictionary.json` (approved words). `flag_tokens()` marks unknown Hebrew words (using `wordfreq` lexicon with prefix stripping). `apply()` performs whole-word regex replacement. `bias_terms()` feeds vocabulary to Whisper's hotwords. `format_bidi()` wraps Latin runs in Unicode directional isolates. |
| `history.py` | **Transcription history.** Thread-safe persistence to `history.json` with stable UUID-based entry IDs. Capped at 300 entries. |
| `config.py` | **Configuration.** Loads `config.json` merged over `DEFAULTS` dict. Keys: hotkey, model, language, device, compute_type, beam_size, beam_size_cpu, cpu_threads, vad_filter, input_device, restore_clipboard, clipboard_restore_delay, max_record_seconds, idle_release_minutes, release_on_fullscreen, sounds, sound_volume, initial_prompt, highlight_unknown, bidi_isolate, theme. |
| `theme.py` | **Design system.** `LIGHT`/`DARK` palette dicts, `build_qss(palette)` generates the app-wide Qt stylesheet, `pick_font()` selects system font (Segoe UI Variable Text → Segoe UI → Inter). |
| `widgets.py` | **Reusable UI widgets.** `FramelessWindow` (rounded + drop shadow + native edge-resize via `startSystemResize`), `TitleBar` (branded, draggable, theme toggle + min/close), `NavRail` (side navigation), `ToggleSwitch` (iOS-style painted toggle), `Card`. |
| `icons.py` | **Programmatic icons.** `pixmap(name, color, size)` renders 2x-scaled line icons via `QPainter` (history, dictionary, settings, copy, trash, refresh, search, mic, sun, moon, minimize, close). No external image assets needed. |
| `tray.py` | **System tray.** `Tray(QObject)` wraps `QSystemTrayIcon` with dynamic mic icon coloring per state (blue=loading, gray=idle, red=recording, yellow=transcribing). Context menu: Settings, Hotkey display, Quit. Thread-safe via signals. |
| `paste.py` | **Text injection.** `paste_text()` saves clipboard, copies text via `pyperclip`, releases held modifier keys, injects `Ctrl+V` via native `keybd_event`, optionally restores clipboard after delay. |
| `sounds.py` | **Audio feedback.** Plays start/stop/error chimes from `app/assets/` WAV files via `sounddevice`. `import_sound()` converts external audio via PyAV (`av`). |
| `fullscreen.py` | **Fullscreen detection.** `foreground_is_fullscreen()` queries Win32 APIs to detect if a fullscreen app (game/video) is in foreground, enabling resource release. |
| `applog.py` | **Logging setup.** `RotatingFileHandler` (1MB × 2 backups) to `mywhisper.log`, plus UTF-8 stdout reconfiguration to prevent Windows charmap crashes with Hebrew. |
| `version.py` | **Version string.** `__version__ = "1.10.0"`. Single source of truth. |

### Developer / Build Tools (`app/`)

| File | Purpose |
|------|---------|
| `make_icon.py` | Generates multi-resolution `app/assets/icon.ico` via QPainter + PNG struct encoding |
| `make_sounds.py` | Downloads audio cues from DigitalOcean and converts to WAV via PyAV |
| `make_og.py` | Renders 1200×630 `docs/og-image.png` OpenGraph preview card via QPainter |
| `make_screens.py` | Renders off-screen UI screenshots (`docs/*.png`) with curated demo data |
| `check_gpu.py` | CLI sanity check: records 4s from mic, transcribes, verifies GPU + Hebrew |
| `gpu_infer_check.py` | Synthetic audio test verifying CUDA/cuBLAS/cuDNN DLL loading |
| `model_check.py` | Non-interactive verification: loads model, runs synthetic noise audio |

### Scripts (`scripts/`)

| File | Purpose |
|------|---------|
| `setup.ps1` | Venv setup: installs Python 3.12 via winget, detects NVIDIA GPU via WMI, creates `.venv`, installs deps, creates default `config.json` |
| `install.ps1` | One-command installer: installs Git, clones repo, runs setup, creates desktop shortcut, starts app |
| `uninstall.ps1` | Uninstaller: kills processes, removes shortcuts and install folder |
| `install_autostart.ps1` | Adds shortcut to Windows Startup folder for auto-launch on login |
| `run_mywishper.vbs` | Silent VBS launcher: parses `pyvenv.cfg`, sets `__PYVENV_LAUNCHER__`, runs as single process |
| `update.ps1` | In-place updater: kills instance, `git pull`, re-runs setup, restarts |
| `MyWhisper-Setup.cmd` | Double-click batch installer invoking `install.ps1` |

### Tests (`tests/`)

| File | Purpose |
|------|---------|
| `test_corrections.py` | Unit tests for `corrections.py`: whole-word replacements, single-pass non-chaining, longest-key priority, approval, bias term capping, bidi wrapping |
| `test_history.py` | Unit tests for `history.py`: ID generation, ordering, update/delete by ID, legacy migration, 300-entry cap |

### Docs & Assets (`docs/`)

| File | Purpose |
|------|---------|
| `index.html` | Hebrew landing page (GitHub Pages): hero, PowerShell install command, features grid, FAQ accordion, download stats |
| `icon.png` | App icon for README |
| `og-image.png` | OpenGraph social preview image |
| `app-*.png` | UI screenshots (history dark/light, dictionary dark, settings light, overlay) |

### Root Files

| File | Purpose |
|------|---------|
| `config.json` | User config (gitignored, per-user). Created from `config.example.json` by setup |
| `config.example.json` | Tracked config template with defaults |
| `corrections.json` | Learned wrong→right word corrections (user data) |
| `dictionary.json` | Approved words list (user data) |
| `history.json` | Transcription history entries with UUIDs (user data) |
| `requirements.txt` | Core Python dependencies |
| `requirements-cuda.txt` | NVIDIA CUDA pip packages (installed only when GPU detected) |
| `CHANGELOG.md` | Version history (rendered in-app via changelog dialog) |
| `README.md` | Hebrew user-facing documentation |
| `CLAUDE.md` | Guidance for Claude Code |
| `.gitignore` | Git ignore rules |

---

## Architecture

### State Machine

`main.py → Mywishper` runs a toggle-based state machine driven by the global hotkey:

```
IDLE ──[hotkey]──► RECORDING ──[hotkey]──► TRANSCRIBING ──► IDLE
                      │                                      ▲
                      └──[Esc / max_record_seconds]──────────┘
```

### Data Flow

```
Hotkey (Win32 RegisterHotKey)
  → Recorder.start() (sounddevice, 16kHz mono float32)
  → Recorder.stop() → numpy audio array
  → Transcriber.transcribe(audio, hotwords) [worker thread]
      → faster-whisper (CUDA float16 / CPU int8 fallback)
      → raw text
  → corrections.apply(text) → corrected text
  → corrections.format_bidi(text) → bidi-formatted text
  → paste_text() (clipboard + Ctrl+V injection)
  → history.add(text)
```

### Threading Model

- **Main thread**: Qt event loop (`QApplication.exec()`), UI rendering, hotkey message processing
- **Worker thread**: Transcription runs on a daemon thread (`_worker`) to keep UI responsive
- **Thread safety**: `threading.Lock` in history; Qt `Signal` for all UI updates from worker threads; `QTimer` for max-record cap (runs on GUI thread)

### Resource Management

`_resource_poll()` is a 5-second `QTimer` that:
1. Releases the Whisper model after `idle_release_minutes` of inactivity
2. Releases the model when a fullscreen app (game) is in foreground (`release_on_fullscreen`)
3. Model reloads transparently on next transcription request

### Single-Instance Guard

A Windows named mutex (`CreateMutexW` via `ctypes`) is acquired at the top of `main.py` before heavy ML imports. A duplicate launch exits instantly.

---

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `faster-whisper` ≥1.0.3 | CTranslate2-based Whisper inference engine |
| `sounddevice` ≥0.4.6 | Cross-platform audio I/O (PortAudio wrapper) |
| `numpy` ≥1.26 | Audio array manipulation |
| `pyperclip` ≥1.8.2 | Cross-platform clipboard access |
| `PySide6-Essentials` ≥6.6 | Qt6 UI toolkit (RTL/bidi support) |
| `wordfreq` ≥3.0 | Offline Hebrew word-frequency lexicon |
| `nvidia-cuda-runtime-cu12` | CUDA runtime DLLs (GPU only, via `requirements-cuda.txt`) |
| `nvidia-cublas-cu12` | cuBLAS DLLs (GPU only) |
| `nvidia-cudnn-cu12` | cuDNN DLLs (GPU only) |

---

## Development Conventions

1. **No linter configured** — keep code style consistent with existing files
2. **RTL-first UI** — the app is globally `RightToLeft`; all UI text, layouts, and HTML must respect RTL
3. **Thread safety** — never touch Qt widgets from worker threads; always use Qt `Signal` to marshal updates to the main thread
4. **Config resilience** — `config.py` merges user config over `DEFAULTS`, so missing keys are fine; never crash on missing config keys
5. **Graceful degradation** — GPU falls back to CPU; missing `wordfreq` disables word flagging; missing mic shows friendly error
6. **Win32 native APIs** — hotkeys use `RegisterHotKey` (not the `keyboard` library); paste uses `keybd_event`; fullscreen detection uses `GetForegroundWindow` + monitor queries. All via `ctypes`
7. **User data files** — `corrections.json`, `dictionary.json`, `history.json` are in the project root; use mtime caching and file-level locking where applicable
8. **Hebrew text** — console output can hit Windows `charmap` encoding errors; this is cosmetic. The app reconfigures stdout to UTF-8 in `applog.py`
9. **CUDA DLL resolution** — `transcriber._add_cuda_dll_dirs()` must run before importing `faster_whisper`, injecting pip CUDA DLL folders into the DLL search path
10. **VBS launcher** — `run_mywishper.vbs` runs the app as a single process (not a venv subprocess) by setting `__PYVENV_LAUNCHER__` and calling the base interpreter directly

---

## GPU / Environment Notes

- GPU inference requires NVIDIA CUDA runtime DLLs from pip packages in `requirements-cuda.txt`. Without them, `faster-whisper` falls back to (slow) CPU
- The global hotkey uses native `RegisterHotKey` (no admin needed). If a combo is already claimed by another app, `RegisterHotKey` fails and the UI/tray surfaces it
- `paste.py` injects `Ctrl+V` via native `keybd_event` — some apps may block input injection; the text remains on clipboard for manual paste
- The `keyboard` library is **not** a dependency (its low-level hook is blocked by security software on some machines)
- First run downloads the Whisper model (~1.5–3 GB) — one-time only

---

## Testing

```powershell
# Run all unit tests
.\.venv\Scripts\python -m unittest discover tests

# Manual smoke tests
.\.venv\Scripts\python app\check_gpu.py       # Records 4s, transcribes
.\.venv\Scripts\python app\model_check.py      # Loads model, runs synthetic audio
.\.venv\Scripts\python app\gpu_infer_check.py  # Verifies CUDA DLL loading
```

Test coverage exists for `corrections.py` and `history.py`. No tests for UI or audio components.
