# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MyWhisper (brand: **Matan Digital**) is a Windows, SuperWhisper-style local Hebrew dictation tool. The user presses a global hotkey anywhere, speaks Hebrew, and the speech is transcribed **locally on the GPU** via `faster-whisper` (with punctuation) and auto-pasted into the focused text field. No internet, no API costs. The README is in Hebrew and is the primary user-facing doc; `docs/index.html` is the GitHub Pages landing page.

## Commands

All Python runs through the isolated venv (`.venv`, Python 3.12 — the ML stack lacks 3.14 wheels). Run from the project root. Operational scripts live in `scripts/` but are all written to `cd` to the repo root themselves (`$root = Split-Path $PSScriptRoot -Parent`), so they can be invoked from anywhere.

```powershell
# One-time setup: creates .venv (Python 3.12) and installs deps; CUDA libs only
# when an NVIDIA card is detected (requirements-cuda.txt is a ~2GB download)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# Sanity check: records 4s from the mic and transcribes (verifies GPU + Hebrew)
# First run downloads the Whisper model (~1.5-3 GB)
.\.venv\Scripts\python app\check_gpu.py

# Run the app
.\.venv\Scripts\python app\main.py

# Run silently to tray (no console window)
wscript scripts\run_mywishper.vbs

# Install/remove Windows autostart (creates MyWhisper.lnk in Startup folder)
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

```powershell
# All unit tests (corrections + history + llm layers)
.\.venv\Scripts\python -m unittest discover tests

# One module / one test (tests/ is a namespace package — no __init__.py needed)
.\.venv\Scripts\python -m unittest tests.test_llm
.\.venv\Scripts\python -m unittest tests.test_llm.LlmFailOpenTestCase.test_no_model_returns_original
```

Tests are pure `unittest` with no network/GPU dependency — `tests/test_llm.py` points the LLM layer at `http://localhost:9` to exercise the fail-open path, so a full run takes ~20s on connection timeouts. Each test file does `sys.path.insert(0, .../app)` and imports modules flat (`import llm`, not `app.llm`). The Qt-touching suites (`test_widgets.py`, `test_clipwatch.py`) set `QT_QPA_PLATFORM=offscreen` at import time and reuse one `QApplication`, so they need no display.

Printing Hebrew from a test or one-off script hits the Windows `charmap` console codec. Prefix with `PYTHONIOENCODING=utf-8` when a script prints Hebrew; `unittest` itself is unaffected.

No linter is configured. `app/check_gpu.py` is the manual smoke test for the transcription pipeline; `app/model_check.py` / `app/gpu_infer_check.py` are similar diagnostics. Runtime logs go to `mywhisper.log` (UTF-8, rotating) via `app/applog.py`.

## Architecture

`app/main.py` is the orchestrator. `Mywishper` wires the components and runs a toggle-based state machine driven by the global hotkey:

1. `HotkeyManager` (`hotkey.py`) registers a global hotkey via the native Win32 `RegisterHotKey` API (not the `keyboard` lib, whose low-level hook is silently blocked by security software on some machines). WM_HOTKEY messages are caught on the Qt main thread via a `QAbstractNativeEventFilter` bound to a hidden host window (`_ensure_host`), de-duplicated (Qt delivers each message to filters twice), and routed to `Mywishper.toggle` (idle → recording → transcribing → idle). `rebind()` re-registers live from the settings UI; `TempHotkey` registers Esc only while recording. The toggle callback runs on the GUI thread, so the max-record cap uses a `QTimer` (not `threading.Timer`). The routing is id-keyed, so **several hotkeys coexist** — `main` registers a second `HotkeyManager` for the clipboard picker (`clipboard_hotkey`), and a failure there is non-fatal so dictation keeps working. `_KEYS` maps punctuation to layout-independent OEM virtual-key codes (`` ` `` → `VK_OEM_3`); `ui._qt_key_name` must stay in sync with it or the settings key-capture will produce combos the parser rejects.
2. First press: `Recorder.start()` (`recorder.py`, `sounddevice`) captures mic audio; start beep; tray + overlay turn red. UI/tray updates from the hotkey/worker threads are marshaled to the Qt main thread via signals.
3. Second press: `Recorder.stop()` returns samples; transcription runs on a worker thread (`_worker`) so the UI stays responsive.
4. `Transcriber.transcribe()` (`transcriber.py`) runs `faster-whisper` on GPU (`cuda`/`float16`, falls back to CPU/`int8`; CPU uses `beam_size_cpu` greedy for speed). It takes two bias inputs: `hotwords=corrections.bias_terms()` and `glossary=corrections.english_terms()` (folded into `initial_prompt` by `_effective_prompt`, capped at 30 terms, gated by config `glossary_prompt`). The model loads lazily (`load`/`ensure_loaded`) and can be freed (`unload`) — `main._resource_poll` (a 5s GUI-thread `QTimer`) releases it after `idle_release_minutes` idle or while a fullscreen app is foreground (`fullscreen.foreground_is_fullscreen`), reloading transparently on next use. `transcriber._add_cuda_dll_dirs()` injects the pip CUDA DLL folders into the DLL search path before importing `faster_whisper` — required or GPU load fails.
5. Post-processing in `_worker` produces the **logical** text, then the **display** text — keep these distinct:
   - `raw = corrections.apply(text)` — deterministic learned fixes (see below).
   - Optional LLM pass (see *Local-LLM polish*): `llm_compare` → both versions labeled via `_compare_text`; `llm_polish` → the polished version; otherwise `raw`.
   - The chosen logical text goes to `history.add()` **as-is**. Only the pasted copy runs through `corrections.format_bidi()` (config `bidi_isolate`), which wraps Latin runs in U+2066/U+2069 isolates so English stays LTR inside Hebrew. Never store isolate characters in history or corrections — they would leak into the learning layer.
   - `paste_text()` (`paste.py`: clipboard + `Ctrl+V` via native `keybd_event`, with optional clipboard restore).

Single-instance guard: a named Windows mutex acquired at the top of `main.py` (before the heavy ML imports); a duplicate launch exits instantly.

UI is **PySide6 (Qt)** — chosen over Tkinter because Tk 8.6 has no real bidi/RTL support (Hebrew + embedded English rendered scrambled and laggy). Qt runs on the **main thread** (`QApplication.exec()` in `main.py`); the tray and hotkey/transcription run off it and talk back via Qt **signals** (`AppUI.set_overlay_state` / `open_settings` / `request_quit` are thread-safe). The app is globally `RightToLeft`. UI layer files:
- `app/theme.py` — design system: `LIGHT`/`DARK` palettes (token dicts), `build_qss(palette)` (one app-wide stylesheet), `pick_font()`, plus the `FS` type scale and `SP`/`RADIUS` spacing tokens. Active theme persisted in config `theme`. **Prefer a named QSS role over an inline stylesheet**: `build_qss` defines `#hint`, `#fieldlabel`, `#cardtext`, `#statusok`, `#toast`, `#morebtn`, and a widget opts in with `setObjectName(...)`. To swap a role at runtime use `ui._set_role()` — it clears any inline sheet first, because an inline rule outranks the global one and would freeze the widget on its old look.
- `app/icons.py` — color-aware line icons drawn with QPainter (`icon(name, color, size)`); no extra deps.
- `app/widgets.py` — reusable `FramelessWindow` (rounded + drop shadow + native edge-resize via `startSystemResize`/`startSystemMove`), `TitleBar` (branded, draggable, theme toggle + min/close), `NavRail` (side nav, emits `selected`, `set_tooltips()`), `ToggleSwitch`, `Card`. `ToggleSwitch` overrides `hitButton()`: it paints its own 46×26 track and carries no label, and QCheckBox's default hit area is only the indicator — so without the override ~84% of the visible switch silently ignored clicks. Any custom-painted QCheckBox here needs the same treatment.
- `app/ui.py` (~2000 lines, by far the largest file — splitting it by page is the obvious next refactor) — `MainWindow(FramelessWindow)` = title bar + nav rail + `QStackedWidget` of three pages: **היסטוריה** (search + per-card copy/delete), **מילון** (learned corrections + the English glossary), **הגדרות** (engine status, theme, mic picker + live test, sounds, LLM polish, hotkey capture via `HotkeyEdit`, clipboard history, updates). `AppUI(QObject)` is the controller — a **pure callback shell**: `main.py` injects every backend function (`english_terms=`, `llm_list_models=`, `model_status`, `clear_clips`, …) and each defaults to a no-op lambda, so `ui.py` never imports the transcription/LLM stack. History cards are rich-text `QLabel` with `dir="rtl"`; each Hebrew word is an `<a href="idx:tok">` link (`on_word_clicked` → `CorrectionDialog`), unknown words styled red. `Overlay` is the frameless recording HUD; `ChangelogDialog` shows "what's new"; `Toast` is the non-blocking bottom-of-window message used for undo-after-delete.

  Performance notes for the history page, all learned by measurement — Qt re-lays out the whole scroll area on **every** insert, so cost tracks cards-on-screen, not history size (100 cards ≈ 490 ms per rebuild, 25 ≈ 150 ms):
  - `HISTORY_PAGE` (25) plus a "הצג עוד" footer; `_page_limit` grows a page at a time and resets on a new query.
  - The search box is debounced (200 ms) — bound straight to `textChanged` it rebuilt every card on every keystroke.
  - `card_html` is memoised in `_html_cache`; **any dictionary change must call `_invalidate_cards()`** or corrected words keep rendering red.
  - A finished transcription calls `prepend_transcription()` (insert one card) rather than a full refresh; `AppUI._history_dirty` defers the work when the window is hidden and `_show_window` catches up.

  Theme switching still rebuilds the window (`_rebuild`) because icons, `ToggleSwitch` and `Overlay` paint from palette colours directly; it preserves the page index **and the scroll offset**.

  `QMessageBox.question()`'s standard buttons render as English "Yes"/"No" in this all-Hebrew UI — build destructive confirmations with explicit `addButton()` calls and make Cancel both the default and the escape button.

The tray (`tray.py`) is a native `QSystemTrayIcon`. Sounds (`sounds.py`, generated by `make_sounds.py`) live in `app/assets/`. The main window opens on launch and is forced to the foreground (`SetForegroundWindow`) since a wscript-launched process can't normally grab focus.

Launch detail: `scripts/run_mywishper.vbs` starts the **base** interpreter directly with `__PYVENV_LAUNCHER__` set to the venv python, so the app runs as a *single* process (a venv launcher would spawn a second) while keeping the venv's `sys.prefix`/site-packages (so CUDA + wordfreq still resolve).

## Self-improving correction layer (`app/corrections.py`)

The accuracy-learning feature. State lives in three JSON files in the project root: `corrections.json` (`{wrong: right}`), `dictionary.json` (approved Hebrew words) and `english_terms.json` (English/tech glossary, seeded with defaults on first use). Flow:

- **Detect**: `flag_tokens(text)` splits a transcription into tokens; a Hebrew word is flagged "unknown" when it's not approved, not a correction target, and absent from the **wordfreq** Hebrew lexicon (offline) — also trying prefix-stripped forms (ו/ה/ב/כ/ל/מ/ש) to cut false positives. The history UI renders each card as a Qt rich-text `QLabel` (`dir="rtl"`) with each Hebrew word an `<a>` link; unknown words are styled red (gated by config `highlight_unknown`). wordfreq lookups are memoized (`_in_lexicon` lru_cache) and the JSON files are mtime-cached so rendering many cards stays fast.
- **Correct**: clicking a word opens a popup → `add_correction(wrong, right)` (also approves `right`) or `approve_word(word)`. The edited entry is rewritten via `apply_corrections` + `history.update(index, text)`. `suggest_similar()` offers close matches from the Hebrew wordlist.
- **Learn**: `apply(text)` whole-word-replaces known mistakes on every future transcription; `bias_terms()` builds the Whisper `hotwords` string with a deliberate priority order — English glossary terms first (highest signal for mixed dictation), then correction targets, then the most recently approved dictionary words filling the remaining slots, bounded at `_MAX_BIAS_TERMS` (100). faster-whisper folds `hotwords` into the prompt alongside `initial_prompt`.

If `wordfreq` is unavailable, detection degrades gracefully (nothing is flagged); the rest still works.

## Local-LLM polish (`app/llm.py`, optional, off by default)

An opt-in second pass that sends the transcription to a locally running **Ollama** (`llm_url`, default `http://localhost:11434`) for a Hebrew editing pass before pasting. Still fully offline — no cloud, no API keys.

- **Fail-open is the contract.** `polish()` never raises: server down, model missing, timeout, empty output, output >2× the input length, or multi-line output (the model rambled/explained) all return the **original** text. Any change here must preserve that — enabling the feature can never block a paste or corrupt the result. `tests/test_llm.py` guards exactly this.
- **Two styles** (`llm_style`): `"correct"` fixes clear spelling/punctuation errors only at temperature 0; `"rewrite"` actively rephrases dictated speech into clean written Hebrew at temperature 0.4 (it needs some freedom or the model just echoes the input). Prompts are single Hebrew directives sent to `/api/generate` — **not** chat roles, which made some models answer the instruction conversationally instead of editing.
- Output is scrubbed for `<think>…</think>` preambles (qwen3/gemma) and echoed Hebrew labels/quotes.
- The LLM path deliberately **skips** `corrections.apply()` — the model phrases on its own, so its output is independent of the manual dictionary. `llm_compare` pastes both versions labeled (`[עם LLM]` / `[מקורי]`) for live A/B evaluation; `_compare_text` collapses to one block when the LLM changed nothing.
- Every call is logged with latency and changed/no-change to `mywhisper.log`.

## Clipboard history (`clips.py`, `clipwatch.py`, `clipui.py`)

A clipboard manager that is **independent of dictation**: `history.py` records what MyWhisper produced, `clips.py` records what the user copied from anywhere. Opened with its own hotkey (`clipboard_hotkey`, default `` ctrl+` ``), which lists every clip with search; choosing one puts it back on the clipboard for the user to paste themselves (it does **not** auto-paste — that was the explicit product decision).

- **`clipwatch.ClipboardWatcher`** hooks `QClipboard.dataChanged` on the Qt main thread. Three things it must never record, in order of importance:
  1. **Secrets.** Password managers tag their clipboard writes with formats like `ExcludeClipboardContentFromMonitorProcessing` and `Clipboard Viewer Ignore` precisely so history tools skip them. Honouring `_SECRET_FORMATS` is what separates this from a password logger — `tests/test_clipwatch.py` covers all five markers, and that coverage should not be weakened.
  2. **Our own writes.** `paste_text()` sets the clipboard and then restores it, so `main._worker` calls `clipwatch.suppress()` around it; without that every dictation would also appear as a "copy". `_use_clip` suppresses too. Suppression is time-boxed, not a counter, because a missed signal would otherwise disable capture forever.
  3. **Anything while paused** — `set_paused()` is user-facing (Settings) and persists via `clipboard_paused`.
- **`clips.py`** is the store (`clips.json` + `clip_images/`). Text is deliberately **uncapped in count** — a long searchable history is the point — but a single entry over `MAX_TEXT_CHARS` (100k) is skipped so one copied log file can't bloat every load, and re-copying an existing string moves it to the top instead of duplicating. Images *are* capped (`MAX_IMAGES`), and eviction deletes the PNG. `delete()` returns `(entry, index)` for undo, mirroring `history.py`.
- **`clipui.ClipPicker`** uses `QListWidget`, not the hand-built cards of the history page: the store is unbounded and QListWidget only realizes visible rows. It centres on the screen under the cursor (not the primary one) and force-calls `SetForegroundWindow`, since a popup raised from a global hotkey has no foreground rights on Windows.

## Config (`config.json`, defaults in `config.py`)

Runtime source of truth is `config.json` (gitignored, per-user; created from the tracked `app/config.example.json` by `setup.ps1`); `config.py` merges it over `DEFAULTS` (so missing keys are fine). **Add a new key in all three places**: `DEFAULTS`, `config.example.json`, and the README key table. The default hotkey is `ctrl+space` everywhere.

Keys by group:
- *Transcription*: `model` (default `ivrit-ai/whisper-large-v3-turbo-ct2`; `large-v3` is the higher-accuracy alternative), `language`=`he`, `device`, `compute_type`, `beam_size`, `beam_size_cpu`, `cpu_threads`, `vad_filter`, `initial_prompt`, `glossary_prompt`.
- *Capture / paste*: `hotkey`, `input_device`, `max_record_seconds` (Esc cancels a recording; the cap auto-stops a forgotten one), `restore_clipboard`, `clipboard_restore_delay`.
- *Resources*: `idle_release_minutes`, `release_on_fullscreen` (both drive `_resource_poll`'s model unload).
- *Clipboard history*: `clipboard_history` (master switch — when off, no watcher and no second hotkey are created at all), `clipboard_hotkey`, `clipboard_paused`.
- *LLM*: `llm_polish`, `llm_compare`, `llm_style`, `llm_model`, `llm_url`, `llm_timeout`.
- *UI*: `theme`, `sounds`, `sound_volume`, `highlight_unknown`, `bidi_isolate`.

## Distribution & updates

`app/version.py` (`__version__`) is the single source of truth, shown in Settings. **Three things must agree on every release: `__version__`, a `## גרסה X.Y.Z` section in `CHANGELOG.md`, and the git tag / GitHub release.** `tests/test_changelog.py` enforces this and fails the suite when they drift — six patch versions had already fallen out of the changelog before it existed, so users on those builds opened "מה חדש" and saw nothing highlighted. Versions deliberately folded into the "גרסאות קודמות" heading are listed in that test's `SUMMARISED` set. `main._check_update` polls the GitHub releases API and `_do_update` launches `scripts/update.ps1` in a visible window, then quits so the updater can replace files: it kills the running `pythonw` for *this* folder, `git pull --ff-only` (falling back to `reset --hard origin/main`), reinstalls deps and relaunches. `scripts/install.ps1` is the one-line web installer (`irm … | iex`) that clones into `%USERPROFILE%\MyWhisper`; `scripts/MyWhisper-Setup.cmd` is the double-clickable wrapper published as a release asset. Bump `__version__` and add a `CHANGELOG.md` entry together — `ChangelogDialog` surfaces it in-app after an update.

## GPU / environment notes

- GPU inference needs the NVIDIA CUDA runtime DLLs from the pip packages `nvidia-cuda-runtime-cu12`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` (installed by `scripts/setup.ps1` only when an NVIDIA card is detected). Without them, `faster-whisper` falls back to (slow) CPU.
- The global hotkey uses native `RegisterHotKey` (no admin needed); `paste.py` injects Ctrl+V via native `keybd_event`. The `keyboard` library is no longer a dependency. If a hotkey combo is already claimed by another app, `RegisterHotKey` fails and the UI/tray surfaces it (pick another combo in Settings).
- Console output can hit Windows `charmap` encoding errors on Hebrew text (see `app_run_log.txt`); this is cosmetic logging, not a transcription failure. `update.ps1` is kept ASCII-only so Windows PowerShell 5.1 parses it regardless of code page.
