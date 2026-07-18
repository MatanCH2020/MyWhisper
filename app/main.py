"""Mywishper — global Hebrew dictation for Windows.

Press the global hotkey once to start recording, again to stop. The speech is
transcribed locally with faster-whisper (Hebrew, with punctuation) and pasted
into whatever field has focus.
"""
import ctypes
import sys
import threading
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

import keyboard
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import corrections
import history
import sounds
from config import load_config, save_config
from recorder import Recorder
from transcriber import Transcriber
from hotkey import HotkeyManager
from ui import AppUI
from paste import paste_text
from tray import Tray


class Mywishper:
    def __init__(self):
        self.config = load_config()
        self._apply_sound_config()

        self.recorder = Recorder()
        # Loaded in the background by start(): on first run the model is a
        # ~2GB download, and blocking here would leave the user with no tray,
        # no window and a dead hotkey for minutes.
        self.transcriber = None
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
        )
        self.tray = Tray(
            on_quit=self.quit,
            on_settings=self.ui.open_settings,
            hotkey=self.config.get("hotkey"),
        )
        self.ui.notify = self.tray.notify  # balloon hints (minimize-to-tray etc.)
        self.hotkeys = HotkeyManager(self.config.get("hotkey"), self.toggle)
        self.ui.set_hotkey = self._set_hotkey            # live hotkey editor
        self.ui.relaunch_as_admin = self._relaunch_as_admin

        self._lock = threading.Lock()
        self._busy = False  # True while transcribing (ignore toggles)
        self._esc_hook = None   # Esc-to-cancel, registered only while recording
        self._max_timer = None  # auto-stop for a forgotten recording

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

    def _relaunch_as_admin(self):
        """Relaunch the app elevated (UAC). Returns False if elevation was
        cancelled or unavailable (e.g. no admin rights on this machine)."""
        import ctypes
        root = Path(__file__).resolve().parent.parent
        vbs = str(root / "run_mywishper.vbs")
        try:
            shell32 = ctypes.windll.shell32
            shell32.ShellExecuteW.restype = ctypes.c_void_p
            r = shell32.ShellExecuteW(None, "runas", "wscript.exe",
                                      f'"{vbs}"', str(root), 1)
        except Exception:
            log.exception("Elevation failed")
            return False
        if int(r) <= 32:  # ShellExecute error / user declined UAC
            return False
        # The elevated instance will take over; quit this one after the UAC
        # dialog clears (by then this process has released the single mutex).
        QTimer.singleShot(600, self.quit)
        return True

    def toggle(self):
        with self._lock:
            if self.transcriber is None:
                self.tray.notify("MyWhisper",
                                 "מודל התמלול עדיין נטען — נסה שוב בעוד רגע.")
                return
            if self._busy:
                return  # mid-transcription, ignore extra presses
            if not self.recorder.recording:
                self._start_recording()
            else:
                self._stop_and_transcribe()

    def _start_recording(self):
        self.recorder.start()
        self.tray.set_state("recording", "MyWhisper — מקליט...")
        self.ui.set_overlay_state("recording")
        sounds.start_recording()
        # Esc cancels the recording without transcribing (hooked only while
        # recording, so Esc behaves normally the rest of the time).
        try:
            self._esc_hook = keyboard.add_hotkey("esc", self.cancel_recording)
        except Exception:
            self._esc_hook = None
        max_sec = self.config.get("max_record_seconds", 600)
        if max_sec and max_sec > 0:
            self._max_timer = threading.Timer(max_sec, self._auto_stop)
            self._max_timer.daemon = True
            self._max_timer.start()

    def _end_recording_hooks(self):
        """Remove the Esc hook and the auto-stop timer (recording is over)."""
        if self._esc_hook is not None:
            try:
                keyboard.remove_hotkey(self._esc_hook)
            except Exception:
                pass
            self._esc_hook = None
        if self._max_timer is not None:
            self._max_timer.cancel()
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

    def _worker(self, audio):
        try:
            text = self.transcriber.transcribe(audio, hotwords=corrections.bias_terms())
            if text:
                # Apply learned corrections before delivering / saving.
                text = corrections.apply(text)
                history.add(text)  # store clean logical text
                out = text
                if self.config.get("bidi_isolate", True):
                    out = corrections.format_bidi(text)  # keep English LTR in RTL
                paste_text(out, self.config.get("restore_clipboard", True),
                           self.config.get("clipboard_restore_delay", 0.5))
                log.info("-> %s", text)
            else:
                log.info("(empty transcription)")
        except Exception:
            log.exception("Transcription failed")
            sounds.error()
        finally:
            self.tray.set_state("idle", "MyWhisper — מוכן")
            self.ui.set_overlay_state("idle")
            self._busy = False

    def start(self):
        self.hotkeys.start()
        self.tray.set_state("loading", "MyWhisper — טוען מודל...")
        threading.Thread(target=self._load_model, daemon=True).start()
        # If loading is still going after a few seconds (first-run download),
        # tell the user what's happening instead of looking dead.
        hint = threading.Timer(4.0, self._loading_hint)
        hint.daemon = True
        hint.start()

    def _loading_hint(self):
        if self.transcriber is None:
            self.tray.notify(
                "MyWhisper — טוען מודל",
                "מודל התמלול נטען ברקע. בהפעלה הראשונה זו הורדה חד-פעמית "
                "של כ-2GB — האייקון במגש יהפוך אפור כשהכול מוכן.")

    def _load_model(self):
        try:
            transcriber = Transcriber(self.config)
        except Exception:
            log.exception("Model load failed")
            self.tray.set_state("idle", "MyWhisper — שגיאה בטעינת המודל")
            self.tray.notify("MyWhisper — שגיאה",
                             "טעינת מודל התמלול נכשלה. בדוק את mywhisper.log.",
                             "warning")
            return
        self.transcriber = transcriber
        self.tray.set_state("idle", "MyWhisper — מוכן")
        log.info("Ready. Press the hotkey to dictate. (Quit from the tray icon.)")
        # A silent GPU->CPU fallback would otherwise only show as 10x slower
        # transcription; surface it.
        if transcriber.fallback_reason:
            self.tray.notify(
                "MyWhisper — מצב CPU",
                "טעינת ה-GPU נכשלה, התמלול ירוץ על המעבד (איטי יותר). "
                "בדוק דרייבר NVIDIA וספריות CUDA (setup.ps1).", "warning")

    def quit(self):
        try:
            self.hotkeys.stop()
        except Exception:
            pass
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
