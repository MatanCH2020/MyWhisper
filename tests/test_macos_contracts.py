"""Portable Mac contracts. No user data, model download, GPU, or real clipboard."""
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import numpy as np
import model_download as download
from local_vad import speech_spans
from macos_hotkey import _parse, HotkeyManager
from macos_updates import select_release, BASE
import mlx_transcriber


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "weights"
        self.payload = b"synthetic-model-data"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.cancel = threading.Event()

    def response(self, data, status=200, headers=None):
        response = io.BytesIO(data)
        response.status, response.headers = status, headers or {}
        return response

    def run_download(self, response, progress=lambda *args: None):
        with patch.object(download.urllib.request, "urlopen", return_value=response) as opening:
            download.download_file("https://example.test/model", self.path, len(self.payload),
                                   self.digest, self.cancel, progress)
        return opening

    def test_resume_requires_exact_content_range_and_checks_hash(self):
        self.path.with_name("weights.part").write_bytes(self.payload[:5])
        call = self.run_download(self.response(self.payload[5:], 206,
                                 {"Content-Range": f"bytes 5-{len(self.payload)-1}/{len(self.payload)}"}))
        self.assertEqual(call.call_args.args[0].get_header("Range"), "bytes=5-")
        self.assertEqual(self.path.read_bytes(), self.payload)

    def test_server_ignoring_range_restarts_without_appending(self):
        self.path.with_name("weights.part").write_bytes(self.payload[:5])
        self.run_download(self.response(self.payload))
        self.assertEqual(self.path.read_bytes(), self.payload)

    def test_corrupt_download_never_becomes_ready(self):
        with self.assertRaises(ValueError):
            self.run_download(self.response(b"x" * len(self.payload)))
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_name("weights.part").exists())

    def test_cancel_preserves_partial_for_resume(self):
        def progress(done, total):
            if done:
                self.cancel.set()
        with self.assertRaises(download.DownloadCancelled):
            self.run_download(self.response(self.payload), progress)
        self.assertFalse(self.path.exists())
        self.assertTrue(self.path.with_name("weights.part").exists())

    def test_valid_download_can_be_used_offline(self):
        self.path.write_bytes(self.payload)
        with patch.object(download.urllib.request, "urlopen", side_effect=AssertionError("network")):
            download.download_file("unused", self.path, len(self.payload), self.digest, self.cancel)

    def test_partial_wrong_range_is_not_appended(self):
        with self.assertRaises(ValueError):
            self.run_download(self.response(self.payload, 206, {"Content-Range": "bytes 9-99/100"}))
        self.assertFalse(self.path.exists())


class MacDecodeTest(unittest.TestCase):
    def transcriber(self, **config):
        with patch.object(mlx_transcriber, "data_dir", return_value=Path("test-model-only")):
            tr = mlx_transcriber.Transcriber({"vad_filter": False, **config})
        tr.model = object()
        tr._tokenizer = SimpleNamespace(encode=lambda text: list(text.encode("utf-8")))
        tr._module = SimpleNamespace(transcribe=Mock(return_value={"text": "בדיקה עם GitHub"}))
        return tr

    def test_single_bounded_prompt_and_supported_decoding_only(self):
        tr = self.transcriber(initial_prompt="שלום " * 100)
        tr.transcribe(np.ones(16000, dtype=np.float32), "GitHub GitHub Python", ["Python", "GitHub"])
        args = tr._module.transcribe.call_args.kwargs
        self.assertEqual(args["language"], "he")
        self.assertEqual(args["task"], "transcribe")
        self.assertEqual(args["temperature"], 0.0)
        self.assertFalse(args["condition_on_previous_text"])
        self.assertIsNone(args["verbose"])
        self.assertNotIn("hotwords", args)
        self.assertNotIn("beam_size", args)
        self.assertEqual(args["initial_prompt"].count("GitHub"), 1)
        self.assertLessEqual(len(tr._tokenizer.encode(" " + args["initial_prompt"])), 112)

    def test_silence_does_not_load_or_decode(self):
        tr = self.transcriber()
        tr.load = Mock(side_effect=AssertionError("load"))
        self.assertEqual(tr.transcribe(np.zeros(16000, dtype=np.float32)), "")
        tr._module.transcribe.assert_not_called()

    def test_vad_no_speech_never_reaches_decoder(self):
        tr = self.transcriber(vad_filter=True)
        tr._vad = SimpleNamespace(filter=lambda audio: np.array([], dtype=np.float32))
        self.assertEqual(tr.transcribe(np.ones(16000, dtype=np.float32)), "")
        tr._module.transcribe.assert_not_called()

    def test_unverified_model_does_not_implicitly_download(self):
        tr = self.transcriber()
        tr.model = None
        tr.download.ready = lambda: False
        tr.download.download = Mock(side_effect=AssertionError("network"))
        with self.assertRaises(RuntimeError):
            tr.load()

    def test_vad_pads_and_merges_without_overlapping_audio(self):
        spans = speech_spans([0] * 8 + [0.9] * 8 + [0] * 25, 41 * 512)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0][0], 8 * 512 - 2560)
        self.assertEqual(speech_spans([0] * 100, 51200), [])


class MacPlatformTest(unittest.TestCase):
    def test_mac_defaults_leave_windows_defaults_intact(self):
        import platform_support as platform
        with patch.object(platform, "IS_MAC", False):
            self.assertEqual(platform.defaults(), {})
        with patch.object(platform, "IS_MAC", True):
            self.assertEqual(platform.defaults()["device"], "metal")
            self.assertEqual(platform.defaults()["clipboard_hotkey"], "ctrl+alt+v")

    def test_hotkey_conflict_retains_old_registration(self):
        manager = HotkeyManager("ctrl+alt+space", Mock())
        manager._id = 7
        with patch("macos_hotkey._register", side_effect=OSError("taken")), patch("macos_hotkey._unregister") as stop:
            with self.assertRaises(OSError):
                manager.rebind("ctrl+alt+v")
        stop.assert_not_called()
        self.assertEqual(manager._id, 7)

    def test_hotkey_rebind_while_paused_does_not_register(self):
        manager = HotkeyManager("ctrl+alt+space", Mock())
        manager._paused = True
        with patch("macos_hotkey._register") as register:
            manager.rebind("cmd+shift+v")
        register.assert_not_called()
        self.assertEqual(_parse(manager.hotkey), (256 | 512, 9))
        with self.assertRaises(ValueError):
            _parse("ctrl+a+b")

    def test_release_channel_requires_mac_asset_and_ignores_stable(self):
        def release(v, **kwargs):
            name = f"MyWhisper-{v}-macos-arm64.dmg"
            return {"tag_name": "v" + v, "prerelease": True,
                    "assets": [{"name": name, "browser_download_url": f"{BASE}/releases/download/v{v}/{name}"}], **kwargs}
        latest, url = select_release([release("1.15.0b2"), release("1.15.0b10"),
                                     release("9.0.0", prerelease=False), release("1.16.0b1", assets=[])])
        self.assertEqual(latest, "1.15.0b10")
        self.assertEqual(url, BASE + "/releases/tag/v1.15.0b10")

    def test_mac_ui_has_local_dictionary_and_no_cloud_controls(self):
        from PySide6.QtWidgets import QApplication
        from ui import AppUI, MainWindow
        import theme
        self.app = QApplication.instance() or QApplication([])
        ui = AppUI({"theme": "dark", "hotkey": "ctrl+alt+space"},
                   lambda: 0, lambda cfg: True, lambda: [], lambda: True, lambda cue: None, lambda *a: True)
        ui.cloud_supported = False
        window = MainWindow(ui, theme.DARK)
        try:
            self.assertFalse(hasattr(window, "_cloud_panel"))
            self.assertFalse(hasattr(window, "_scan_panel"))
            self.assertTrue(hasattr(window, "_eng_input"))
            window.set_palette(theme.LIGHT)
        finally:
            window.hide()
            window.deleteLater()
