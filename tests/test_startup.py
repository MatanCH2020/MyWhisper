"""Background startup must not open a window or announce unusable dictation."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtWidgets import QApplication
import main

qapp = QApplication.instance() or QApplication([])


class StartupTest(unittest.TestCase):
    def app(self, startup=True, hotkey=True):
        app = Mock()
        app._closing = False
        app._model_ever_ready = False
        app._startup = startup
        app._hotkey_ready = hotkey
        app._state = main.State.IDLE
        app.recorder.recording = False
        app.transcriber.fallback_reason = None
        app.config = {"hotkey": "ctrl+space"}
        return app

    def test_ready_only_when_model_hotkey_and_mic_ready(self):
        for startup, hotkey, mic, expected in (
                (True, True, True, True), (False, True, True, False),
                (True, False, True, False), (True, True, False, False)):
            with self.subTest(startup=startup, hotkey=hotkey, mic=mic):
                app = self.app(startup, hotkey)
                with patch.object(main, "has_input_device", return_value=mic):
                    main.Mywishper._finish_load(app, True)
                self.assertEqual(app.tray.notify.called, expected)

    def test_selected_microphone_must_exist(self):
        app = self.app()
        app.config["input_device"] = "USB Mic"
        with patch.object(main, "list_input_devices", return_value=[(1, "Other Mic")]):
            main.Mywishper._finish_load(app, True)
        app.tray.notify.assert_not_called()

    def test_failure_and_cpu_fallback_keep_warning(self):
        app = self.app()
        main.Mywishper._finish_load(app, False)
        self.assertEqual(app.tray.notify.call_args.args[-1], "warning")
        app = self.app()
        app.transcriber.fallback_reason = "CUDA unavailable"
        main.Mywishper._finish_load(app, True)
        app.tray.notify.assert_called_once()
        self.assertEqual(app.tray.notify.call_args.args[-1], "warning")

    def test_reload_does_not_repeat_ready_notification(self):
        app = self.app()
        with patch.object(main, "has_input_device", return_value=True):
            main.Mywishper._finish_load(app, True)
            main.Mywishper._finish_load(app, True)
        app.tray.notify.assert_called_once()
        self.assertIn("ctrl+space", app.tray.notify.call_args.args[1])

    def test_only_manual_launch_opens_main_window(self):
        for args, visible in ((["main.py"], True), (["main.py", "--startup"], False)):
            with self.subTest(args=args):
                with patch.object(main.sys, "argv", args), \
                     patch.object(main.sys, "exit"), \
                     patch.object(main, "QApplication") as application, \
                     patch.object(main, "Mywishper") as backend, \
                     patch.object(main.QTimer, "singleShot") as later:
                    main.main()
                    backend.assert_called_once_with(startup=not visible)
                    backend.return_value.start.assert_called_once()
                    self.assertEqual(later.called, visible)
                    application.instance.return_value.exec.assert_called_once()
