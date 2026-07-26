"""Mywishper — global Hebrew dictation for Windows.

Press the global hotkey once to start recording, again to stop. The speech is
transcribed locally with faster-whisper (Hebrew, with punctuation) and pasted
into whatever field has focus.
"""
import ctypes
import subprocess
import sys
import threading
import time
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


if not _acquire_single_instance():
    print("[mywishper] Another instance is already running. Exiting.")
    sys.exit(0)

import applog
applog.setup()  # before the component imports so their import-time logs are captured

import logging
log = logging.getLogger("main")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QApplication

import clips
import corrections
import history
import llm
import sounds
from config import load_config, save_config
from recorder import Recorder, has_input_device, list_input_devices, MicMonitor
from transcriber import Transcriber
from fullscreen import foreground_is_fullscreen
from hotkey import HotkeyManager, TempHotkey
from ui import AppUI
from paste import paste_text
from tray import Tray


class Mywishper:
    def __init__(self):
        self.config = load_config()
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
            llm_list_models=llm.list_models,
        )
        self.tray = Tray(
            on_quit=self.quit,
            on_settings=self.ui.open_settings,
            hotkey=self.config.get("hotkey"),
        )
        self.ui.notify = self.tray.notify  # balloon hints (minimize-to-tray etc.)
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
        self.ui.clear_clips = clips.clear
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

        self._lock = threading.Lock()
        self._busy = False  # True while transcribing (ignore toggles)
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

    def _on_settings_change(self, config):
        """Called from the settings UI when sound options change: apply + persist."""
        self.config = config
        self._apply_sound_config()
        save_config(self.config)

    def _set_hotkey(self, new_hotkey):
        """Live-rebind the global hotkey from the settings UI. Returns True on
        success, False if the combo is invalid / rejected by the OS."""
        new_hotkey = (new_hotkey or "").strip().lower()
        if not new_hotkey:
            return False
        if new_hotkey == self.config.get("hotkey"):
            return True
        try:
            self.hotkeys.rebind(new_hotkey)
        except Exception:
            log.exception("Failed to set hotkey '%s'", new_hotkey)
            return False
        self.config["hotkey"] = new_hotkey
        save_config(self.config)
        self.tray.set_hotkey_label(new_hotkey)
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
                on_clear=clips.clear)
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
        """Launch the in-place updater in a visible window, then quit so it can
        replace the running process. Returns False if it couldn't be launched."""
        updater = Path(__file__).resolve().parent.parent / "scripts" / "update.ps1"
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(updater)],
                cwd=str(updater.parent))
        except Exception:
            log.exception("Failed to launch updater")
            return False
        QTimer.singleShot(800, self.quit)
        return True

    def toggle(self):
        log.info("Hotkey toggle triggered.")
        self._mark_used()
        with self._lock:
            if not self.transcriber.is_loaded():
                if not self._model_ever_ready:
                    # First-ever load may still be downloading — don't record yet.
                    self.tray.notify("MyWhisper",
                                     "מודל התמלול עדיין נטען — נסה שוב בעוד רגע.")
                    return
                # Released to save resources: warm it up now; the transcription
                # worker waits for it. Recording itself needs no model.
                self._start_load_async()
            if self._busy:
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
        self._end_recording_hooks()
        try:
            self.recorder.stop()
        except Exception:
            pass
        self._busy = False
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
            if self._busy or not self.recorder.recording:
                return
            self._end_recording_hooks()
            self.recorder.stop()  # audio discarded
            sounds.stop_recording()
            self.tray.set_state("idle", "MyWhisper — מוכן")
            self.ui.set_overlay_state("idle")
            log.info("Recording cancelled (Esc).")

    def _auto_stop(self):
        """Stop-and-transcribe when the recording cap is reached (forgotten mic)."""
        with self._lock:
            if self._busy or not self.recorder.recording:
                return
            log.warning("Max recording length reached — stopping automatically.")
            self._stop_and_transcribe()

    def _stop_and_transcribe(self):
        self._busy = True
        self._end_recording_hooks()
        audio = self.recorder.stop()
        sounds.stop_recording()
        self.tray.set_state("transcribing", "MyWhisper — מתמלל...")
        self.ui.set_overlay_state("transcribing")
        # Run the heavy work off the hotkey thread so the UI stays responsive.
        threading.Thread(target=self._worker, args=(audio,), daemon=True).start()

    @staticmethod
    def _compare_text(polished, raw):
        """Two labeled blocks for A/B compare mode. If the LLM changed nothing,
        say so instead of pasting the identical text twice."""
        if polished.strip() == raw.strip():
            return f"[עם LLM — ללא שינוי]\n{raw}"
        return f"[עם LLM]\n{polished}\n\n[מקורי]\n{raw}"

    def _worker(self, audio):
        try:
            text = self.transcriber.transcribe(
                audio, hotwords=corrections.bias_terms(),
                glossary=corrections.english_terms())
            if text:
                model = self.config.get("llm_model")
                have_llm = bool(model)
                # The deterministic "raw" version (Hebrew model + learned fixes).
                raw = corrections.apply(text)

                def _polished():
                    # The LLM phrases on its own — the manual dictionary is NOT
                    # applied here, so its output is independent of learned fixes.
                    return llm.polish(
                        text, model, self.config.get("llm_url", llm.DEFAULT_URL),
                        self.config.get("llm_timeout", 20),
                        style=self.config.get("llm_style", "correct"))

                if self.config.get("llm_compare") and have_llm:
                    # A/B mode: paste both versions labeled for live comparison.
                    logical = self._compare_text(_polished(), raw)
                elif self.config.get("llm_polish") and have_llm:
                    logical = _polished()
                else:
                    logical = raw

                history.add(logical)  # store what was actually delivered
                # Refresh an open history page so the new card appears on its
                # own (no-op when the window is closed / hidden).
                self.ui.notify_transcription()
                out = logical
                if self.config.get("bidi_isolate", True):
                    out = corrections.format_bidi(logical)  # keep English LTR in RTL
                # paste_text writes to the clipboard (and restores it after), so
                # mute the watcher — otherwise every dictation would also land in
                # the clipboard history as if the user had copied it.
                if self.clipwatch:
                    self.clipwatch.suppress(
                        2.0 + float(self.config.get("clipboard_restore_delay", 0.5)))
                paste_text(out, self.config.get("restore_clipboard", True),
                           self.config.get("clipboard_restore_delay", 0.5))
                log.info("-> %s", logical)
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
        finally:
            self.tray.set_state("idle", "MyWhisper — מוכן")
            self.ui.set_overlay_state("idle")
            self._busy = False

    def start(self):
        try:
            self.hotkeys.start()
        except Exception:
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
            self._loading = False
            self.tray.set_state("idle", "MyWhisper — שגיאה בטעינת המודל")
            self.tray.notify("MyWhisper — שגיאה",
                             "טעינת מודל התמלול נכשלה. בדוק את mywhisper.log.",
                             "warning")
            return
        self._loading = False
        first_ready = not self._model_ever_ready
        self._model_ever_ready = True
        self._mark_used()  # start the idle countdown now that it's loaded
        # Don't clobber an active recording/transcribing state if this was a
        # background reload triggered mid-use.
        if not self.recorder.recording and not self._busy:
            self.tray.set_state("idle", "MyWhisper — מוכן")
        log.info("Model ready. Press the hotkey to dictate. (Quit from the tray icon.)")
        # A silent GPU->CPU fallback would otherwise only show as 10x slower
        # transcription; surface it once (on the first load).
        if first_ready and self.transcriber.fallback_reason:
            self.tray.notify(
                "MyWhisper — מצב CPU",
                "טעינת ה-GPU נכשלה, התמלול ירוץ על המעבד (איטי יותר). "
                "בדוק דרייבר NVIDIA וספריות CUDA (setup.ps1).", "warning")

    # ---- resource management: release the model when idle / gaming ----
    def _mark_used(self):
        self._last_used = time.monotonic()

    def _resource_poll(self):
        """On the GUI thread every few seconds: free the model when the app has
        been idle or a fullscreen game/video is in the foreground."""
        if (self._busy or self._loading or self.recorder.recording
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
    app = Mywishper()  # loads the Whisper model
    app.start()
    # Open the window shortly after the event loop starts so it's visibly "there"
    # on launch (it also lives in the tray; closing the window keeps it running).
    QTimer.singleShot(300, app.ui.open_settings)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
