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

from PySide6.QtCore import QTimer
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
        self.transcriber = Transcriber(self.config)  # loads model into GPU memory
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
        self.hotkeys = HotkeyManager(self.config.get("hotkey"), self.toggle)

        self._lock = threading.Lock()
        self._busy = False  # True while transcribing (ignore toggles)

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

    def toggle(self):
        with self._lock:
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

    def _stop_and_transcribe(self):
        self._busy = True
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
                paste_text(out, self.config.get("restore_clipboard", True))
                print(f"[mywishper] -> {text}")
            else:
                print("[mywishper] (empty transcription)")
        except Exception as e:
            print(f"[mywishper] Error: {e}")
            sounds.error()
        finally:
            self.tray.set_state("idle", "MyWhisper — מוכן")
            self.ui.set_overlay_state("idle")
            self._busy = False

    def start(self):
        self.hotkeys.start()
        print("[mywishper] Ready. Press the hotkey to dictate. (Quit from the tray icon.)")

    def quit(self):
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        self.ui.request_quit()  # ask the Qt event loop to quit


def main():
    # QApplication must exist before any widget (tray / overlay / windows).
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)  # closing settings keeps the tray alive
    app = Mywishper()  # loads the Whisper model
    app.start()
    # Open the window shortly after the event loop starts so it's visibly "there"
    # on launch (it also lives in the tray; closing the window keeps it running).
    QTimer.singleShot(300, app.ui.open_settings)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
