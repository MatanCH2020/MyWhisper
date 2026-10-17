"""Fullscreen suspends native recording registration, including load races."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QObject
import main
import hotkey
import fullscreen


class HotkeyPauseTest(unittest.TestCase):
    def test_pause_unregisters_and_resume_uses_updated_combo_once(self):
        manager = hotkey.HotkeyManager("ctrl+space", lambda: None)
        with patch.object(hotkey, "_register", side_effect=[1, 2]) as register, \
             patch.object(hotkey, "_unregister") as unregister:
            manager.start()
            manager.start()
            manager.pause()
            unregister.assert_called_once_with(1)
            manager.rebind("ctrl+shift+space")
            manager.start()
            self.assertEqual(register.call_count, 1)
            manager.resume()
            manager.resume()
            self.assertEqual(register.call_count, 2)
            self.assertEqual(register.call_args.args[0], "ctrl+shift+space")

    def test_invalid_rebind_stays_paused_and_resume_conflict_keeps_no_id(self):
        manager = hotkey.HotkeyManager("ctrl+space", lambda: None)
        manager.pause()
        with self.assertRaises(ValueError):
            manager.rebind("ctrl+invalid")
        self.assertTrue(manager._paused)
        self.assertEqual(manager.hotkey, "ctrl+space")
        with patch.object(hotkey, "_register", side_effect=OSError("taken")):
            with self.assertRaises(OSError):
                manager.resume()
        self.assertIsNone(manager._id)


class FullscreenPauseTest(unittest.TestCase):
    def setUp(self):
        self.app = main.Mywishper.__new__(main.Mywishper)
        QObject.__init__(self.app)
        a = self.app
        a.config = {"release_on_fullscreen": True, "idle_release_minutes": 0}
        a._closing = a._updating = a._loading = a._fullscreen_paused = False
        a._hotkey_ready = a._model_ever_ready = True
        a._state = main.State.IDLE
        a._last_used = time.monotonic()
        a._lock = threading.Lock()
        a.hotkeys, a.recorder, a.transcriber, a.ui, a.tray = (Mock() for _ in range(5))
        a.recorder.recording = False
        a.transcriber.is_loaded.return_value = True
        self.detect = patch.object(main, "foreground_is_fullscreen", return_value=True).start()
        self.addCleanup(patch.stopall)

    def test_idle_entry_unregisters_and_unloads_even_without_idle_timeout(self):
        self.app._resource_poll()
        self.app.hotkeys.pause.assert_called_once()
        self.app.transcriber.unload.assert_called_once()
        self.app.ui.set_overlay_state.assert_called_with("idle")
        self.assertFalse(self.app._hotkey_ready)

    def test_queued_hotkey_cannot_record_or_reload_before_poll(self):
        self.app.transcriber.is_loaded.return_value = False
        with patch.object(self.app, "_start_load_async") as load:
            self.app.toggle()
        load.assert_not_called()
        self.app.recorder.start.assert_not_called()
        self.app.tray.notify.assert_not_called()

    def test_unloaded_model_still_resumes_shortcut_without_loading(self):
        self.app._resource_poll()
        self.app.transcriber.is_loaded.return_value = False
        self.detect.return_value = False
        self.app._resource_poll()
        self.app._resource_poll()
        self.app.hotkeys.resume.assert_called_once()
        self.app.transcriber.load.assert_not_called()
        self.assertTrue(self.app._hotkey_ready)

    def test_inflight_load_is_released_on_completion_without_ready_notification(self):
        self.app._loading = True
        self.app._resource_poll()
        self.app.transcriber.unload.assert_not_called()
        self.app._finish_load(True)
        self.app.transcriber.unload.assert_called_once()
        self.app.tray.notify.assert_not_called()

    def test_inflight_transcription_finishes_then_releases(self):
        self.app._state = main.State.TRANSCRIBING
        self.app._resource_poll()
        self.app.transcriber.unload.assert_not_called()
        self.app._finish_transcription()
        self.app.transcriber.unload.assert_called_once()

    def test_recording_is_finished_in_background_without_live_overlay(self):
        self.app.recorder.recording = True
        self.app._state = main.State.RECORDING
        with patch.object(self.app, "_stop_and_transcribe") as stop:
            self.app._resource_poll()
        stop.assert_called_once_with(background=True)
        self.app.ui.set_overlay_state.assert_called_once_with("idle")

    def test_disabled_option_preserves_normal_dictation(self):
        self.app.config["release_on_fullscreen"] = False
        self.app._resource_poll()
        self.app.hotkeys.pause.assert_not_called()
        self.app.transcriber.unload.assert_not_called()

    def test_resume_conflict_warns_once_without_loading_or_crashing(self):
        self.app._resource_poll()
        self.detect.return_value = False
        self.app.hotkeys.resume.side_effect = OSError("taken")
        self.app._resource_poll()
        self.app._resource_poll()
        self.app.tray.notify.assert_called_once()
        self.assertFalse(self.app._hotkey_ready)

    def test_starting_in_fullscreen_never_loads_model(self):
        self.app.transcriber.is_loaded.return_value = False
        with patch.object(main.threading, "Thread") as worker:
            self.app._start_load_async()
        worker.assert_not_called()
        self.assertFalse(self.app._loading)

    def test_result_saved_without_paste_or_balloon_before_poll(self):
        self.app.transcriber.transcribe.return_value = "sample"
        self.app.clipwatch = None
        with patch.object(main.corrections, "bias_terms", return_value=""), \
             patch.object(main.corrections, "english_terms", return_value=[]), \
             patch.object(main.corrections, "apply", side_effect=lambda t: t), \
             patch.object(main.history, "add") as history, \
             patch.object(main.clips, "add_text", return_value={}) as clips, \
             patch.object(main, "same_destination", return_value=True), \
             patch.object(main, "paste_text") as paste:
            self.app._worker([0], 123)
        history.assert_called_once_with("sample")
        clips.assert_called_once_with("sample")
        paste.assert_not_called()
        self.app.tray.notify.assert_not_called()


class FullscreenDetectionTest(unittest.TestCase):
    def test_geometry_on_each_monitor_and_normal_maximized_window(self):
        for window, monitor, expected in (
            ((0, 0, 1920, 1080), (0, 0, 1920, 1080), True),
            ((-1080, -226, 0, 1694), (-1080, -226, 0, 1694), True),
            ((-8, -8, 1928, 1040), (0, 0, 1920, 1080), False),
            ((100, 100, 800, 600), (0, 0, 1920, 1080), False),
        ):
            with self.subTest(window=window):
                user = Mock()
                user.GetForegroundWindow.return_value = 0x100000001
                user.GetClassNameW.side_effect = lambda h, b, n: setattr(b, "value", "Demo") or 4
                def rectangle(h, ptr):
                    for key, value in zip(("left", "top", "right", "bottom"), window):
                        setattr(ptr._obj, key, value)
                    return 1
                def info(h, ptr):
                    for key, value in zip(("left", "top", "right", "bottom"), monitor):
                        setattr(ptr._obj.rcMonitor, key, value)
                    return 1
                user.GetWindowRect.side_effect = rectangle
                user.GetMonitorInfoW.side_effect = info
                with patch.object(fullscreen, "_user32", user):
                    self.assertEqual(fullscreen.foreground_is_fullscreen(), expected)

    def test_shell_and_unknown_windows_do_not_pause(self):
        user = Mock()
        user.GetForegroundWindow.return_value = 0
        with patch.object(fullscreen, "_user32", user):
            self.assertFalse(fullscreen.foreground_is_fullscreen())
            user.GetForegroundWindow.return_value = 1
            user.GetClassNameW.side_effect = lambda h, b, n: setattr(b, "value", "Progman") or 7
            self.assertFalse(fullscreen.foreground_is_fullscreen())
            user.GetClassNameW.side_effect = OSError("unavailable")
            self.assertFalse(fullscreen.foreground_is_fullscreen())

