"""Mywishper — global Hebrew dictation for Windows.

Press the global hotkey once to start recording, again to stop. The speech is
transcribed locally with faster-whisper (Hebrew, with punctuation) and pasted
into whatever field has focus.
"""
import ctypes
import enum
import os
import updater
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

# Allow running as `python app/main.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Qt manages per-monitor DPI awareness itself, so no manual ctypes DPI call.

# Single-instance guard: a named Windows mutex. CreateMutexW succeeds in every
# process, but only the first one creates it fresh; any later process gets
# ERROR_ALREADY_EXISTS and bails out. This is more reliable than a socket bind
# and is checked *before* the heavy ML imports so a duplicate launch exits
# instantly instead of loading the model and flashing a second tray icon.
_MUTEX_NAME = "MyWhisper_MatanDigital_SingleInstance_v1"
_instance_mutex = None  # kept alive for the process lifetime (OS frees on exit)


def _acquire_single_instance():
    global _instance_mutex
    try:
        kernel32 = ctypes.windll.kernel32
        _instance_mutex = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        ERROR_ALREADY_EXISTS = 183
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:
        return True  # never block startup if the guard itself errors


if __name__ == "__main__" and not _acquire_single_instance():
    print("[mywishper] Another instance is already running. Exiting.")
    sys.exit(0)

import applog
if __name__ == "__main__":
    applog.setup()

import logging
log = logging.getLogger("main")

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QApplication

import clips
import corrections
import history
import sounds
import safe_json
import theme
from config import load_config, save_config
from recorder import Recorder, has_input_device, list_input_devices, MicMonitor
from transcriber import Transcriber
from fullscreen import foreground_is_fullscreen
from hotkey import HotkeyManager, TempHotkey
from ui import AppUI
from paste import paste_text
from tray import Tray
from cloud_http import CloudHTTP
from chatgpt_auth import ChatGPTAuth, AuthError, DEFAULT_MODEL
from history_scanner import HistoryScanner
from text_editor import TextEditor
from delivery import foreground_window, same_destination, DestinationGuard
from external_browser import browser_choices, open_external_browser


class State(enum.Enum):
    """Explicit states for the toggle-based dictation state machine."""
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class Mywishper(QObject):
    _worker_finished = Signal()
    _load_finished = Signal(bool)
    _storage_error = Signal(str)
    _update_prepared = Signal(str, str)

    def __init__(self, startup=False):
        super().__init__()
        self._startup = startup
        self._hotkey_ready = False
        self._closing = False
        self._updating = False
        self._update_prepared.connect(self._launch_update)
        self._worker_finished.connect(self._finish_transcription)
        self._load_finished.connect(self._finish_load)
        self._storage_error.connect(self._show_storage_error)
        self.config = load_config()
        self.cloud_http = CloudHTTP()
        self.chatgpt = ChatGPTAuth(self.cloud_http)
        self.text_editor = TextEditor(self.chatgpt, self.cloud_http)
        self.history_scanner = HistoryScanner(self.chatgpt, self.cloud_http)
        self._set_chatgpt_enabled(self.config.get("chatgpt_enabled") is True)
        self._cloud_timer = QTimer(self)
        self._cloud_timer.timeout.connect(self.chatgpt.schedule_refresh)
        self._cloud_timer.start(30000)
        self.chatgpt.schedule_refresh()
        self._apply_sound_config()

        self.recorder = Recorder(self.config.get("input_device") or None)
        # The transcriber object exists immediately, but its model is loaded in
        # the background by start() (on first run it's a ~2GB download) and may
        # be released later to free the GPU/CPU when idle or a game is running.
        self.transcriber = Transcriber(self.config)
        self.ui = AppUI(
            self.config,
            level_provider=self.recorder.get_level,
            on_change=self._on_settings_change,
            get_history=history.load,
            clear_history=history.clear,
            test_sound=sounds.play,
            import_sound=sounds.import_sound,
            flag_tokens=corrections.flag_tokens,
            add_correction=corrections.add_correction,
            approve_word=corrections.approve_word,
            list_corrections=corrections.list_corrections,
            remove_correction=corrections.remove_correction,
            apply_corrections=corrections.apply,
            format_bidi=corrections.format_bidi,
            update_history=history.update,
            delete_history=history.delete,
            restore_history=history.restore,
            suggest_similar=corrections.suggest_similar,
            english_terms=corrections.english_terms,
            add_english_term=corrections.add_english_term,
            remove_english_term=corrections.remove_english_term,
        )
        self.tray = Tray(
            on_quit=self.quit,
            on_settings=self.ui.open_settings,
            hotkey=self.config.get("hotkey"),
            palette=self.ui.p,
        )
        self.ui.notify = self.tray.notify  # balloon hints (minimize-to-tray etc.)
        safe_json.set_error_handler(self._storage_error.emit)
        self.hotkeys = HotkeyManager(self.config.get("hotkey"), self.toggle)
        # Clipboard history: a watcher on the Qt clipboard plus its own hotkey.
        self.clipwatch = None
        self.clip_hotkeys = None
        self._clip_picker = None
        if self.config.get("clipboard_history", True):
            from clipwatch import ClipboardWatcher
            self.clipwatch = ClipboardWatcher()
            self.clipwatch.set_paused(bool(self.config.get("clipboard_paused")))
            self.clip_hotkeys = HotkeyManager(
                self.config.get("clipboard_hotkey", "ctrl+`"), self.show_clips)
        self.ui.clip_paused = lambda: bool(self.clipwatch and self.clipwatch.is_paused())
        self.ui.set_clip_paused = self._set_clip_paused
        self.ui.clear_clips = self.clipwatch.clear_history if self.clipwatch else clips.clear
        self.ui.clip_count = lambda: len(clips.load())
        self.ui.set_hotkey = self._set_hotkey            # live hotkey editor
        self.ui.list_input_devices = lambda: [n for _, n in list_input_devices()]
        self.ui.set_input_device = self._set_input_device
        self.mic_monitor = MicMonitor()          # live level meter for the mic test
        self.ui.mic_test_start = self._mic_test_start
        self.ui.mic_test_stop = self.mic_monitor.stop
        self.ui.mic_level = self.mic_monitor.level
        self.ui.model_status = self._model_status
        self.ui.check_update = self._check_update
        self.ui.do_update = self._do_update
        self.ui.chatgpt_status = self._chatgpt_status
        self.ui.chatgpt_action = self._chatgpt_action
        self.ui.chatgpt_browsers = browser_choices
        self.ui.scan_history = self._scan_history
        self.ui.cancel_history_scan = self.history_scanner.cancel
        self.ui.undo_history_scan = self.history_scanner.undo
        self.ui.can_undo_history_scan = self.history_scanner.can_undo
        self.ui.history_scan_report = self.history_scanner.report
        self.ui.prepare_history_scan = self.history_scanner.prepare

        self._lock = threading.Lock()
        self._state = State.IDLE
        self._esc_hook = None   # Esc-to-cancel, registered only while recording
        self._max_timer = None  # auto-stop for a forgotten recording
        self._loading = False           # model load in progress
        self._model_ever_ready = False  # first successful load happened
        self._last_used = time.monotonic()
        self._resource_timer = None     # idle / fullscreen release poll

    def _apply_sound_config(self):
        sounds.configure(
            enabled=self.config.get("sounds", True),
            volume=self.config.get("sound_volume", 0.25),
        )

    def _chatgpt_status(self):
        status = self.chatgpt.status()
        status["model"] = self.config.get("chatgpt_model", "")
        return status

    def _set_chatgpt_enabled(self, enabled):
        status = self.chatgpt.status()
        allowed = (status["connected"] and status["eligible"] and not status["error"]
                   and self.config.get("chatgpt_model") in {m["slug"] for m in status["models"]})
        enabled = enabled is True and bool(allowed)
        self.chatgpt.set_enabled(enabled)
        self.text_editor.cancel()
        if hasattr(self, "history_scanner"):
            self.history_scanner.cancel()
        self.config["chatgpt_enabled"] = enabled
        if not save_config(self.config):
            self.chatgpt.set_enabled(False)
            self.config["chatgpt_enabled"] = False
            enabled = False
        if enabled:
            self.chatgpt.schedule_refresh()
        return enabled

    def _scan_history(self, progress, ticket=None):
        result = self.history_scanner.scan(self.config.get("chatgpt_model", ""), detail=progress, ticket=ticket)
        if result.status in ("quota", "authorization", "ineligible"):
            self.chatgpt.error = result.status
            self._set_chatgpt_enabled(False)
        log.info("History scan status=%s scanned=%d corrected=%d learned=%d english=%d",
                 result.status, result.scanned, result.corrected, result.learned, result.english)
        return asdict(result)

    def _chatgpt_action(self, action, value=None):
        """Injected callback. Network actions are invoked on a UI worker thread."""
        message = ""
        try:
            if action == "enable":
                if value is True and self.chatgpt.error in ("quota", "unsupported"):
                    self.chatgpt.error = ""
                if not self._set_chatgpt_enabled(value) and value is True:
                    message = "העריכה כבויה. יש לחבר חשבון זכאי ולבחור מודל זמין."
            elif action == "model":
                self._set_chatgpt_enabled(False)
                if value in {m["slug"] for m in self.chatgpt.status()["models"]}:
                    self.config["chatgpt_model"] = value
                    save_config(self.config)
            elif action in ("login", "relogin", "select", "disconnect"):
                self._set_chatgpt_enabled(False)
                if action in ("login", "relogin"):
                    self.chatgpt.sign_in(returning=action == "relogin", browser=lambda url:
                        open_external_browser(url, self.config.get("chatgpt_browser", "system")))
                    message = ("החשבון מחובר. השימוש במסגרת חשבון ChatGPT ובכפוף למכסתו. "
                               "העריכה עדיין כבויה; אפשר להפעיל אותה בנפרד.")
                    if not self.chatgpt.status()["eligible"]:
                        message = "החשבון מחובר, אך לא אושר שימוש במסגרת ChatGPT או שאינו זכאי. התמלול ממשיך מקומית."
                elif action == "select":
                    self.chatgpt.select(value)
                    if self.chatgpt.status()["connected"]:
                        self.chatgpt.catalog()
                else:
                    if not self.chatgpt.disconnect():
                        message = ("נותקת במחשב. ביטול ההרשאה בשרת לא אושר; "
                                   "אפשר לנתק את MyWhisper בהגדרות ChatGPT.")
                models = {m["slug"] for m in self.chatgpt.status()["models"]}
                self.config["chatgpt_model"] = DEFAULT_MODEL if DEFAULT_MODEL in models else ""
                save_config(self.config)
            elif action == "welcome":
                with self.chatgpt.lock:
                    self.chatgpt.data["welcome_seen"] = True
                    self.chatgpt.store.save(self.chatgpt.data)
            elif action == "browser":
                if value in {b["slug"] for b in browser_choices()}:
                    self.config["chatgpt_browser"] = value
                    save_config(self.config)
            elif action == "usage":
                if not open_external_browser("https://chatgpt.com/settings/usage", self.config.get("chatgpt_browser", "system")):
                    message = "לא ניתן לפתוח דפדפן חיצוני. בחר דפדפן מותקן בהגדרות ההתחברות."
            elif action == "catalog":
                self.chatgpt.catalog()
                if self.config.get("chatgpt_model") not in {m["slug"] for m in self.chatgpt.status()["models"]}:
                    self._set_chatgpt_enabled(False)
                    self.config["chatgpt_model"] = ""
                    save_config(self.config)
            elif action == "cancel":
                self.chatgpt._signin_cancel.set()
        except AuthError as error:
            log.info("ChatGPT action %s did not complete (%s)", action, str(error))
            message = {
                "permission": "החשבון לא אישר שימוש במסגרת ChatGPT או אינו זכאי. התמלול ממשיך מקומית.",
                "identity": "אימות זהות החשבון נכשל. יש להתחבר מחדש.",
                "authorization": "ההרשאה פגה או נדחתה. יש להתחבר מחדש.",
                "storage": "לא ניתן לקרוא או לשמור את ההרשאות המוצפנות. העריכה נשארת כבויה.",
                "timeout": "ההתחברות לא הושלמה בזמן. אפשר לנסות שוב.",
                "cancelled": "ההתחברות בוטלה.",
                "browser": "לא ניתן לפתוח את הדפדפן להתחברות.",
            }.get(str(error), "החיבור לא הושלם. אפשר לנסות שוב; התמלול ממשיך מקומית.")
        except Exception:
            log.info("ChatGPT action %s did not complete", action)
            message = "החיבור לא הושלם. בדוק את האינטרנט ונסה שוב; התמלול ממשיך מקומית."
        status = self._chatgpt_status()
        return {**status, "message": message,
                "needs_welcome": status["eligible"] and not self.chatgpt.data.get("welcome_seen", False)}

    def _on_settings_change(self, config):
        """Called from the settings UI when sound options change: apply + persist."""
        self.config = config
        self._apply_sound_config()
        palette = theme.palette(self.config.get("theme", "dark"))
        self.tray.set_palette(palette)
        if self._clip_picker is not None:
            self._clip_picker.set_palette(palette)
        return save_config(self.config)

    @Slot(str)
    def _show_storage_error(self, message):
        if not self._closing:
            self.tray.notify("MyWhisper — שמירת נתונים", message, "warning")

    def _set_hotkey(self, new_hotkey):
        """Live-rebind the global hotkey from the settings UI. Returns True on
        success, False if the combo is invalid / rejected by the OS."""
        new_hotkey = (new_hotkey or "").strip().lower()
        if not new_hotkey:
            return False
        if new_hotkey == self.config.get("hotkey"):
            return True
        previous = self.config.get("hotkey")
        try:
            self.hotkeys.rebind(new_hotkey)
        except Exception:
            log.exception("Failed to set hotkey '%s'", new_hotkey)
            return False
        self.config["hotkey"] = new_hotkey
        if not save_config(self.config):
            self.config["hotkey"] = previous
            try:
                self.hotkeys.rebind(previous)
            except Exception:
                log.exception("Could not restore the previous hotkey binding")
            return False
        self.tray.set_hotkey_label(new_hotkey)
        self._hotkey_ready = True
        log.info("Hotkey changed to '%s'.", new_hotkey)
        return True

    def _set_input_device(self, name):
        """Change the microphone used for recording (from the settings UI)."""
        self.recorder.set_device(name or None)
        self.config["input_device"] = name or ""
        save_config(self.config)
        log.info("Input device set to %r.", name or "system default")

    def _mic_test_start(self, name):
        """Open the given mic for the live level meter. False if it won't open."""
        try:
            self.mic_monitor.start(name or None)
            return True
        except Exception:
            log.exception("Mic test failed to open device %r", name)
            return False

    # ---- clipboard history ----
    def show_clips(self):
        """Open the clip picker (clipboard hotkey). Built on first use."""
        if self._clip_picker is None:
            from clipui import ClipPicker
            self._clip_picker = ClipPicker(
                self.ui.p, on_pick=self._use_clip,
                on_delete=lambda cid: clips.delete(cid),
                on_clear=self.ui.clear_clips, on_theme=self.ui.toggle_theme)
        if self._clip_picker.isVisible():
            self._clip_picker.hide()   # same key closes it again
            return
        self._clip_picker.show_for(clips.load())

    def _use_clip(self, entry):
        """Put a chosen clip back on the clipboard for the user to paste."""
        cb = QApplication.clipboard()
        # Our own write — don't let the watcher re-record it as a fresh copy.
        if self.clipwatch:
            self.clipwatch.suppress(2.0)
        try:
            if entry.get("kind") == "image":
                img = QImage(entry.get("path", ""))
                if img.isNull():
                    log.warning("Clip image missing: %s", entry.get("path"))
                    return
                cb.setImage(img)
            else:
                cb.setText(entry.get("text", ""))
        except Exception:
            log.exception("Failed to put a clip on the clipboard")

    def _set_clip_paused(self, paused):
        if self.clipwatch:
            self.clipwatch.set_paused(paused)
        self.config["clipboard_paused"] = bool(paused)
        save_config(self.config)

    def _model_status(self):
        """Snapshot of the transcription engine for the settings UI.

        state: 'loading' while a load is in flight, 'ready' once the model sits
        in memory, 'released' after _resource_poll freed it (idle / fullscreen).
        A released model is normal, not an error — it reloads on the next press.
        """
        if self._loading:
            state = "loading"
        elif self.transcriber.is_loaded():
            state = "ready"
        else:
            state = "released"
        return {
            "state": state,
            "device": self.transcriber.device or "",
            "model": self.config.get("model", ""),
            "fallback": self.transcriber.fallback_reason,
        }

    def _check_update(self):
        """Return the latest published version string (e.g. '1.8.1'), or None on
        failure. Called from a worker thread by the settings UI."""
        try:
            import json
            import urllib.request
            url = "https://api.github.com/repos/MatanCH2020/MyWhisper/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "MyWhisper"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return (json.load(r).get("tag_name") or "").lstrip("v") or None
        except Exception:
            log.exception("Update check failed")
            return None

    def _do_update(self):
        """Prepare in the background while the current installation stays usable."""
        if self._updating or self._state != State.IDLE or self._loading:
            return False
        self._updating = True
        root = Path(__file__).resolve().parent.parent
        def prepare():
            try:
                tag = updater.prepare(root)
                self._update_prepared.emit(tag, "")
            except Exception as exc:
                self._update_prepared.emit("", str(exc))
        threading.Thread(target=prepare, daemon=True).start()
        return True

    @Slot(str, str)
    def _launch_update(self, tag, error):
        if self._closing:
            return
        if error:
            self._updating = False
            log.warning("Update preflight failed: %s", error)
            self.tray.notify("MyWhisper — העדכון נעצר", error, "warning")
            return
        script = Path(__file__).resolve().parent.parent / "scripts" / "update.ps1"
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(script), "-Tag", tag, "-ParentPid", str(os.getpid())],
                cwd=str(script.parent), creationflags=subprocess.CREATE_NEW_CONSOLE)
        except OSError:
            self._updating = False
            log.exception("Could not start updater")
            self.tray.notify("MyWhisper", "לא ניתן להפעיל את העדכון.", "warning")
            return
        self.quit()


    def toggle(self):
        if self._closing or self._updating:
            return
        log.info("Hotkey toggle triggered.")
        self._mark_used()
        with self._lock:
            if self._state == State.TRANSCRIBING:
                return
            if not self.transcriber.is_loaded():
                if not self._model_ever_ready:
                    self._start_load_async()
                    # First-ever load may still be downloading — don't record yet.
                    self.tray.notify("MyWhisper",
                                     "מודל התמלול עדיין נטען — נסה שוב בעוד רגע.")
                    return
                # Released to save resources: warm it up now; the transcription
                # worker waits for it. Recording itself needs no model.
                self._start_load_async()
            if self._state == State.TRANSCRIBING:
                return  # mid-transcription, ignore extra presses
            try:
                if not self.recorder.recording:
                    self._start_recording()
                else:
                    self._stop_and_transcribe()
            except Exception:
                # Most often: no microphone / no default input device, so
                # sounddevice fails to open the stream. Surface it instead of
                # dying silently inside the hotkey callback.
                log.exception("Recording toggle failed")
                self._recover_from_error()

    def _recover_from_error(self):
        guard = getattr(self, "_destination_guard", None)
        if guard is not None:
            guard.close()
            self._destination_guard = None
        self._end_recording_hooks()
        try:
            self.recorder.stop()
        except Exception:
            pass
        self._state = State.IDLE
        self.tray.set_state("idle", "MyWhisper — שגיאה")
        self.ui.set_overlay_state("idle")
        sounds.error()
        self.tray.notify(
            "MyWhisper — בעיית מיקרופון",
            "לא ניתן להתחיל הקלטה. ודא שמחובר מיקרופון והוא מוגדר כהתקן הקלט "
            "ברירת המחדל ב-Windows (הגדרות ← מערכת ← קול). פרטים ב-mywhisper.log.",
            "warning")

    def _start_recording(self):
        self.recorder.start()
        self._state = State.RECORDING
        self.tray.set_state("recording", "MyWhisper — מקליט...")
        self.ui.set_overlay_state("recording")
        sounds.start_recording()
        # Esc cancels the recording without transcribing (registered only while
        # recording, so Esc behaves normally the rest of the time). Runs on the
        # GUI thread, same as the toggle, since both arrive via WM_HOTKEY.
        self._esc_hook = TempHotkey("esc", self.cancel_recording)
        self._esc_hook.start()
        max_sec = self.config.get("max_record_seconds", 600)
        if max_sec and max_sec > 0:
            # QTimer (not threading.Timer) so _auto_stop fires on the GUI thread
            # — hotkey (un)registration must stay on the thread that owns it.
            self._max_timer = QTimer()
            self._max_timer.setSingleShot(True)
            self._max_timer.timeout.connect(self._auto_stop)
            self._max_timer.start(int(max_sec * 1000))

    def _end_recording_hooks(self):
        """Remove the Esc hotkey and the auto-stop timer (recording is over)."""
        if self._esc_hook is not None:
            self._esc_hook.stop()
            self._esc_hook = None
        if self._max_timer is not None:
            self._max_timer.stop()
            self._max_timer = None

    def cancel_recording(self):
        """Discard the current recording without transcribing (Esc)."""
        with self._lock:
            if self._state == State.TRANSCRIBING or not self.recorder.recording:
                return
            self._end_recording_hooks()
            try:
                self.recorder.stop()  # audio discarded
            except Exception:
                log.exception("Microphone failed while cancelling")
            finally:
                self._state = State.IDLE
            sounds.stop_recording()
            self.tray.set_state("idle", "MyWhisper — מוכן")
            self.ui.set_overlay_state("idle")
            log.info("Recording cancelled (Esc).")

    def _auto_stop(self):
        """Stop-and-transcribe when the recording cap is reached (forgotten mic)."""
        with self._lock:
            if self._state == State.TRANSCRIBING or not self.recorder.recording:
                return
            log.warning("Max recording length reached — stopping automatically.")
            try:
                self._stop_and_transcribe()
            except Exception:
                log.exception("Microphone failed during automatic stop")
                self._recover_from_error()

    def _stop_and_transcribe(self):
        self._destination_guard = DestinationGuard().start()
        destination = self._destination_guard
        self._state = State.TRANSCRIBING
        self._end_recording_hooks()
        audio = self.recorder.stop()
        sounds.stop_recording()
        self.tray.set_state("transcribing", "MyWhisper — מתמלל...")
        self.ui.set_overlay_state("transcribing")
        # Run the heavy work off the hotkey thread so the UI stays responsive.
        threading.Thread(target=self._worker, args=(audio, destination), daemon=True).start()

    def _worker(self, audio, destination=None):
        if destination is None:
            destination = foreground_window()
        try:
            text = self.transcriber.transcribe(
                audio, hotwords=corrections.bias_terms(),
                glossary=corrections.english_terms())
            if text:
                logical = corrections.apply(text)
                original = logical
                edit = None
                if self.config.get("chatgpt_enabled") is True:
                    self.ui.set_overlay_state("polishing")
                    edit = self.text_editor.edit(logical, self.config.get("chatgpt_model", ""))
                    logical = edit.text
                    if edit.status in ("quota", "ineligible", "authorization", "unsupported"):
                        self.chatgpt.error = edit.status
                        self._set_chatgpt_enabled(False)
                    if edit.status not in ("edited", "unchanged", "disabled"):
                        hint = {"timeout": "העריכה התעכבה", "quota": "מכסת ChatGPT אינה זמינה; העריכה כובתה",
                                "ineligible": "החשבון אינו זכאי; העריכה כובתה",
                                "authorization": "נדרש חיבור מחדש לחשבון ChatGPT; העריכה כובתה"}.get(
                                    edit.status, "העריכה לא הושלמה")
                        self.tray.notify("MyWhisper", hint + " — נשמר התמלול המקומי.", duration_ms=3500)
                history.add(logical, **({"original_text": original, "edit_status": edit.status,
                                        "edit_ms": edit.elapsed_ms, "edit_model": edit.model} if edit else {}))
                # Refresh an open history page so the new card appears on its
                # own (no-op when the window is closed / hidden).
                self.ui.notify_transcription()
                destination_check = (destination.unchanged if isinstance(destination, DestinationGuard)
                                     else lambda: same_destination(destination))
                if not destination_check():
                    self.tray.notify("MyWhisper — הטקסט מוכן",
                                     "החלון הפעיל השתנה. הטקסט נשמר בהיסטוריה ומוכן להעתקה.")
                    return
                out = logical
                if self.config.get("bidi_isolate", True):
                    out = corrections.format_bidi(logical)  # keep English LTR in RTL
                # paste_text writes to the clipboard (and restores it after), so
                # mute the watcher — otherwise every dictation would also land in
                # the clipboard history as if the user had copied it.
                if self.clipwatch:
                    self.clipwatch.suppress(
                        2.0 + float(self.config.get("clipboard_restore_delay", 0.5)))
                delivered = paste_text(out, self.config.get("restore_clipboard", True),
                           self.config.get("clipboard_restore_delay", 0.5),
                           destination_check=destination_check)
                if delivered is False:
                    self.tray.notify("MyWhisper — הטקסט מוכן", "החלון הפעיל השתנה. הטקסט ממתין בהיסטוריה.")
                    log.info("Transcription saved; destination changed before paste")
                else:
                    log.info("Transcription delivered (%d characters)", len(logical))
            else:
                log.info("(empty transcription)")
                sounds.error()
                self.tray.notify(
                    "MyWhisper — לא זוהה דיבור",
                    "ההקלטה לא הכילה קול. ודא שנבחר המיקרופון הנכון ובדוק אותו "
                    "ב-הגדרות ← מיקרופון ← בדוק מיקרופון.", "warning")
        except Exception:
            log.exception("Transcription failed")
            sounds.error()
            self.tray.notify("MyWhisper — התמלול לא הושלם",
                             "אפשר לנסות שוב. אם הטקסט כבר נשמר, הוא זמין בהיסטוריה.", "warning")
        finally:
            self._worker_finished.emit()

    @Slot()
    def _finish_transcription(self):
        guard = getattr(self, "_destination_guard", None)
        if guard is not None:
            guard.close()
            self._destination_guard = None
        self._state = State.IDLE
        self._mark_used()
        if not self._closing:
            self.tray.set_state("idle", "MyWhisper — מוכן")
            self.ui.set_overlay_state("idle")

    def start(self):
        try:
            self.hotkeys.start()
            self._hotkey_ready = True
        except Exception:
            self._hotkey_ready = False
            log.exception("Hotkey registration failed")
            hk = self.config.get("hotkey")
            QTimer.singleShot(1500, lambda: self.tray.notify(
                "MyWhisper — הקיצור תפוס",
                f"לא ניתן לרשום את הקיצור '{hk}' — כנראה תפוס בתוכנה אחרת. "
                "פתח הגדרות ← קיצור מקלדת ובחר צירוף אחר.", "warning"))
        if self.clip_hotkeys is not None:
            # Non-fatal: dictation must still work if only this combo is taken.
            try:
                self.clip_hotkeys.start()
            except Exception:
                log.exception("Clipboard hotkey registration failed")
                ck = self.config.get("clipboard_hotkey")
                QTimer.singleShot(2000, lambda: self.tray.notify(
                    "MyWhisper — קיצור הלוח תפוס",
                    f"לא ניתן לרשום את '{ck}' להיסטוריית ההעתקות — תפוס בתוכנה "
                    "אחרת. אפשר לבחור צירוף אחר בהגדרות.", "warning"))
        if not has_input_device():
            QTimer.singleShot(2500, lambda: self.tray.notify(
                "MyWhisper — לא נמצא מיקרופון",
                "לא זוהה התקן הקלטה. חבר מיקרופון והגדר אותו כברירת מחדל ב-Windows "
                "(הגדרות ← מערכת ← קול), אחרת ההקלטה לא תעבוד.", "warning"))
        self._start_load_async()
        # If loading is still going after a few seconds (first-run download),
        # tell the user what's happening instead of looking dead.
        hint = threading.Timer(4.0, self._loading_hint)
        hint.daemon = True
        hint.start()
        # Poll for idle / fullscreen to release the model (runs on the GUI thread).
        self._resource_timer = QTimer()
        self._resource_timer.timeout.connect(self._resource_poll)
        self._resource_timer.start(5000)

    def _start_load_async(self):
        """Load (or reload) the model in the background if not already loading."""
        if self._loading or self.transcriber.is_loaded():
            return
        self._loading = True
        self.tray.set_state("loading", "MyWhisper — טוען מודל...")
        threading.Thread(target=self._load_model, daemon=True).start()

    def _loading_hint(self):
        if not self.transcriber.is_loaded():
            self.tray.notify(
                "MyWhisper — טוען מודל",
                "מודל התמלול נטען ברקע. בהפעלה הראשונה זו הורדה חד-פעמית "
                "של כ-2GB — האייקון במגש יהפוך אפור כשהכול מוכן.")

    def _load_model(self):
        try:
            self.transcriber.load()
        except Exception:
            log.exception("Model load failed")
            self._load_finished.emit(False)
            return
        self._load_finished.emit(True)

    @Slot(bool)
    def _finish_load(self, succeeded):
        self._loading = False
        if self._closing:
            return
        if not succeeded:
            self.tray.set_state("idle", "MyWhisper — שגיאה בטעינת המודל")
            self.tray.notify("MyWhisper — שגיאה",
                             "טעינת מודל התמלול נכשלה. בדוק את mywhisper.log.",
                             "warning")
            return
        first_ready = not self._model_ever_ready
        self._model_ever_ready = True
        self._mark_used()  # start the idle countdown now that it's loaded
        # Don't clobber an active recording/transcribing state if this was a
        # background reload triggered mid-use.
        if not self.recorder.recording and self._state == State.IDLE:
            self.tray.set_state("idle", "MyWhisper — מוכן")
        log.info("Model ready. Press the hotkey to dictate. (Quit from the tray icon.)")
        # A silent GPU->CPU fallback would otherwise only show as 10x slower
        # transcription; surface it once (on the first load).
        if first_ready and self.transcriber.fallback_reason:
            self.tray.notify(
                "MyWhisper — מצב CPU",
                "טעינת ה-GPU נכשלה, התמלול ירוץ על המעבד (איטי יותר). "
                "בדוק דרייבר NVIDIA וספריות CUDA (setup.ps1).", "warning")
        elif first_ready and self._startup and self._hotkey_ready:
            mic_name = self.config.get("input_device")
            mic_available = (any(name == mic_name for _, name in list_input_devices())
                             if mic_name else has_input_device())
            if mic_available:
                self.tray.notify(
                    "MyWhisper — מוכן להכתבה",
                    f"התוכנה פועלת ברקע. להקלטה לחץ {self.config.get('hotkey', 'ctrl+space')}.",
                    duration_ms=4000)

    # ---- resource management: release the model when idle / gaming ----
    def _mark_used(self):
        self._last_used = time.monotonic()

    def _resource_poll(self):
        """On the GUI thread every few seconds: free the model when the app has
        been idle or a fullscreen game/video is in the foreground."""
        if (self._state != State.IDLE or self._loading
                or not self.transcriber.is_loaded()):
            return
        mins = self.config.get("idle_release_minutes", 10)
        if mins and (time.monotonic() - self._last_used) >= mins * 60:
            self._release_model("idle")
            return
        if self.config.get("release_on_fullscreen", True) and foreground_is_fullscreen():
            self._release_model("fullscreen app")

    def _release_model(self, reason):
        self.transcriber.unload()
        self.tray.set_state("idle", "MyWhisper — במצב חיסכון (לחץ קיצור להעיר)")
        log.info("Model released to free resources (%s).", reason)

    def quit(self):
        # Let an in-flight transcription finish and paste before tearing down Qt.
        if self._state == State.TRANSCRIBING or self._loading:
            self.tray.notify("MyWhisper", "יש להמתין לסיום הפעולה לפני יציאה או עדכון.")
            return
        self._closing = True
        self._cloud_timer.stop()
        self.chatgpt.close()
        self.text_editor.cancel()
        self.history_scanner.cancel()
        self.cloud_http.close()
        if self.recorder.recording:
            self.cancel_recording()
        self.mic_monitor.stop()
        if self.clip_hotkeys:
            self.clip_hotkeys.stop()
        if self.clipwatch:
            self.clipwatch.close()
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        if self._resource_timer is not None:
            self._resource_timer.stop()
        self._end_recording_hooks()
        self.tray.stop()  # hide now, or the icon ghosts in the tray until hover
        self.ui.request_quit()  # ask the Qt event loop to quit


def main():
    # QApplication must exist before any widget (tray / overlay / windows).
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)  # closing settings keeps the tray alive
    # Own taskbar identity: without an explicit AppUserModelID Windows groups
    # the window under python.exe and shows the Python icon.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MatanDigital.MyWhisper")
    except Exception:
        pass
    icon_path = Path(__file__).resolve().parent / "assets" / "icon.ico"
    if icon_path.exists():
        qapp.setWindowIcon(QIcon(str(icon_path)))
    startup = "--startup" in sys.argv[1:]
    app = Mywishper(startup=startup)  # loads the Whisper model
    app.start()
    # Open the window shortly after the event loop starts so it's visibly "there"
    # on launch (it also lives in the tray; closing the window keeps it running).
    if not startup:
        QTimer.singleShot(300, app.ui.open_settings)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
