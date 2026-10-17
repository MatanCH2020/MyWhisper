"""Unified picker persistence and capture diagnostics, without real user data."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import numpy as np
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication
import clips
import main
import recorder
import theme
from clipui import ClipPicker
from transcriber import Transcriber


class UnifiedStoreTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "clips.json"
        for attr, value in (("CLIPS_PATH", self.path), ("IMAGE_DIR", Path(folder.name) / "images")):
            patcher = patch.object(clips, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_import_preserves_dates_images_and_retries_without_duplicates(self):
        def save(path):
            path.write_bytes(b"demo")
            return True
        image = clips.add_image(save)
        copied = clips.add_text("same")
        items = [{"text": "older", "time": "2020-01-01 12:00"},
                 {"text": "same", "time": "2019-01-01 12:00"}]
        self.assertTrue(clips.import_transcriptions(items))
        self.assertTrue(clips.import_transcriptions(items))
        entries = clips.load()
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1]["time"], "2020-01-01 12:00")
        self.assertIn(image, entries)
        self.assertIn(copied, entries)

    def test_failed_import_can_retry(self):
        items = [{"text": "legacy", "time": "2020-01-01 12:00"}]
        with patch.object(clips, "_write", return_value=False):
            self.assertFalse(clips.import_transcriptions(items))
        self.assertEqual(clips.load(), [])
        self.assertTrue(clips.import_transcriptions(items))
        self.assertEqual(len(clips.load()), 1)


class PipelineTest(unittest.TestCase):
    def run_pipeline(self, *, enabled=True, destination=True, paste_failure=False):
        app = main.Mywishper.__new__(main.Mywishper)
        QObject.__init__(app)
        app.config = {"clipboard_history": enabled, "clipboard_paused": True, "bidi_isolate": True}
        app.transcriber, app.ui, app.tray = Mock(), Mock(), Mock()
        app.transcriber.transcribe.return_value = "logical"
        app.clipwatch = Mock()
        events = []
        with patch.object(main.corrections, "bias_terms", return_value=""), \
             patch.object(main.corrections, "english_terms", return_value=[]), \
             patch.object(main.corrections, "apply", side_effect=lambda s: s), \
             patch.object(main.corrections, "format_bidi", return_value="display"), \
             patch.object(main.history, "add", side_effect=lambda *a, **k: events.append("history")), \
             patch.object(main.clips, "add_text", side_effect=lambda s: events.append(s) or {}) as store, \
             patch.object(main, "same_destination", return_value=destination), \
             patch.object(main, "paste_text", side_effect=lambda *a, **k: events.append("paste") or (False if paste_failure else True)):
            app._worker([0], 123)
        return events, store

    def test_saved_before_paste_even_when_capture_paused(self):
        events, store = self.run_pipeline()
        self.assertEqual(events, ["history", "logical", "paste"])
        store.assert_called_once_with("logical")

    def test_changed_destination_and_failed_paste_keep_text(self):
        for destination, failure in ((False, False), (True, True)):
            events, store = self.run_pipeline(destination=destination, paste_failure=failure)
            store.assert_called_once_with("logical")
            self.assertEqual(events[:2], ["history", "logical"])

    def test_disabled_history_does_not_store_clip(self):
        events, store = self.run_pipeline(enabled=False)
        store.assert_not_called()
        self.assertEqual(events, ["history", "paste"])


class CaptureTest(unittest.TestCase):
    def test_missing_selected_microphone_never_opens_default(self):
        with patch.object(recorder, "resolve_device", return_value=None), \
             patch.object(recorder.sd, "InputStream") as stream:
            for target in (recorder.Recorder("missing"), recorder.MicMonitor()):
                with self.assertRaises(recorder.MicrophoneUnavailable):
                    target.start() if isinstance(target, recorder.Recorder) else target.start("missing")
            stream.assert_not_called()

    def test_silent_audio_skips_decoder_and_retains_numeric_diagnostics(self):
        tr = Transcriber({"model": "test"})
        with patch.object(tr, "ensure_loaded") as load:
            self.assertEqual(tr.transcribe(np.zeros(16000, dtype=np.float32)), "")
            load.assert_not_called()
        self.assertTrue(tr.last_diagnostics["silent"])
        self.assertEqual(tr.last_diagnostics["duration"], 1)

    def test_non_silent_empty_decode_records_vad_duration(self):
        tr = Transcriber({"model": "test"})
        tr.model = Mock(max_length=448)
        tr.model.hf_tokenizer.encode.return_value = SimpleNamespace(ids=[])
        tr.model.transcribe.return_value = (iter([]), SimpleNamespace(duration_after_vad=0))
        self.assertEqual(tr.transcribe(np.ones(16000, dtype=np.float32) * 0.1), "")
        self.assertFalse(tr.last_diagnostics["silent"])
        self.assertEqual(tr.last_diagnostics["speech_seconds"], 0)
        self.assertTrue(tr.model.transcribe.call_args.kwargs["vad_filter"])


class PickerRefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_refresh_preserves_query_and_selected_id(self):
        picker = ClipPicker(theme.DARK, on_pick=lambda e: None)
        self.addCleanup(picker.close)
        entries = [{"id": str(i), "kind": "text", "text": "match " + str(i), "time": ""} for i in range(4)]
        picker.show_for(entries)
        picker.search.setText("match")
        picker._apply_filter()
        picker.list.setCurrentRow(2)
        selected = picker.list.currentItem().data(Qt.UserRole)
        picker.refresh_entries([dict(entries[0], id="new")] + entries)
        self.assertEqual(picker.search.text(), "match")
        self.assertEqual(picker.list.currentItem().data(Qt.UserRole), selected)
        self.assertEqual(picker.list.count(), 5)

    def test_settings_preserves_missing_microphone_selection(self):
        from ui import AppUI, MainWindow
        ctl = AppUI({"theme": "dark", "input_device": "missing"},
                    level_provider=lambda: 0, on_change=lambda c: None,
                    get_history=lambda: [], clear_history=lambda: None,
                    test_sound=lambda n: None, import_sound=lambda *a: False)
        ctl.list_input_devices = lambda: ["available"]
        win = MainWindow(ctl, theme.DARK)
        self.addCleanup(win.close)
        self.assertEqual(win._mic_combo.currentData(), "missing")
        self.assertIn("לא מחובר", win._mic_combo.currentText())
