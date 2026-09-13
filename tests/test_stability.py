"""Regression tests for real failure paths; no microphone/GPU/clipboard access."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
import config
import corrections
import history
import clips
import main
import paste
import winclipboard
import recorder
import safe_json
from clipwatch import ClipboardWatcher

qapp = QApplication.instance() or QApplication([])


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "data.json"
        self.notices = []
        self.handler = safe_json._handler
        safe_json.set_error_handler(self.notices.append)

    def tearDown(self):
        safe_json._handler = self.handler
        self.tmp.cleanup()

    def test_replace_failure_preserves_original_and_removes_temp(self):
        self.path.write_text('{"old":1}')
        with patch.object(safe_json.os, "replace", side_effect=PermissionError):
            self.assertFalse(safe_json.atomic_save(self.path, {"new": 2}))
        self.assertEqual(json.loads(self.path.read_text()), {"old": 1})
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertTrue(self.notices)

    def test_serialization_failure_preserves_original(self):
        self.path.write_text("[]")
        self.assertFalse(safe_json.atomic_save(self.path, {"bad": float("inf")}))
        self.assertEqual(self.path.read_text(), "[]")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_corrupt_history_is_backed_up_before_next_add(self):
        self.path.write_text("{broken")
        with patch.object(history, "HISTORY_PATH", self.path):
            self.assertEqual(history.load(), [])
            self.assertTrue(history.add("recovered"))
        backups = list(self.root.glob("*.corrupt-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "{broken")

    def test_failed_backup_blocks_destructive_recovery(self):
        self.path.write_text("{broken")
        with patch.object(safe_json.shutil, "copy2", side_effect=PermissionError):
            safe_json.read_json(self.path, [])
            self.assertFalse(safe_json.atomic_save(self.path, []))
        self.assertEqual(self.path.read_text(), "{broken")

    def test_history_does_not_report_failed_delete(self):
        with patch.object(history, "HISTORY_PATH", self.path):
            history.add("keep")
            cid = history.load()[0]["id"]
            with patch.object(safe_json.os, "replace", side_effect=PermissionError):
                self.assertIsNone(history.delete(cid))
            self.assertEqual(history.load()[0]["text"], "keep")

    def test_clear_removes_deleted_image_orphans(self):
        with patch.object(clips, "CLIPS_PATH", self.path), patch.object(clips, "IMAGE_DIR", self.root / "images"):
            def save(path):
                path.write_bytes(b"synthetic image")
                return True
            entry = clips.add_image(save)
            clips.delete(entry["id"])
            self.assertTrue(clips.clear())
            self.assertFalse(Path(entry["path"]).exists())

    def test_image_eviction_waits_for_committed_index(self):
        with patch.object(clips, "CLIPS_PATH", self.path), patch.object(clips, "IMAGE_DIR", self.root / "images"), patch.object(clips, "MAX_IMAGES", 1):
            def save(path):
                path.write_bytes(b"image")
                return True
            first = clips.add_image(save)
            with patch.object(safe_json.os, "replace", side_effect=PermissionError):
                self.assertIsNone(clips.add_image(save))
            self.assertTrue(Path(first["path"]).exists())
            self.assertEqual(len(clips.load()), 1)

    def test_no_deletion_outside_image_directory(self):
        self.path.write_text("keep")
        with patch.object(clips, "IMAGE_DIR", self.root / "images"):
            self.assertFalse(clips._remove_file(self.path))
        self.assertTrue(self.path.exists())

    def test_invalid_configuration_recovers(self):
        for payload in ('null', '[1]', '{"beam_size":1e999}', '{"clipboard_restore_delay":1e999}',
                        '{"sounds":"false","theme":null,"max_record_seconds":-1}'):
            with self.subTest(payload=payload), patch.object(config, "CONFIG_PATH", self.path):
                self.path.write_text(payload)
                loaded = config.load_config()
                self.assertEqual(loaded["beam_size"], config.DEFAULTS["beam_size"])
                self.assertEqual(loaded["clipboard_restore_delay"], 0.5)
                self.assertIs(loaded["sounds"], True)

    def test_config_preserves_unknown_keys(self):
        with patch.object(config, "CONFIG_PATH", self.path):
            self.assertTrue(config.save_config({"future": [1, 2], "theme": "light"}))
            self.assertEqual(config.load_config()["future"], [1, 2])

    def test_correction_snapshot_survives_concurrent_removal(self):
        with patch.object(corrections, "CORRECTIONS_PATH", self.path), patch.object(corrections, "DICTIONARY_PATH", self.root / "dict.json"):
            corrections._corr_cache.update(mtime=-1.0, data={})
            corrections._dict_cache.update(mtime=-1.0, list=[], set=set())
            corrections.add_correction("mistake", "fix")
            compile_real = corrections.re.compile
            class Interleave:
                def __init__(self, pattern):
                    self.pattern = compile_real(pattern)
                def sub(self, fn, text):
                    corrections.remove_correction("mistake")
                    return self.pattern.sub(fn, text)
            with patch.object(corrections.re, "compile", side_effect=Interleave):
                self.assertEqual(corrections.apply("mistake"), "fix")


class RecorderTest(unittest.TestCase):
    def test_start_failure_closes_stream_and_next_start_works(self):
        stream = MagicMock()
        stream.start.side_effect = [RuntimeError("unplugged"), None]
        with patch.object(recorder.sd, "InputStream", return_value=stream), patch.object(recorder, "resolve_device", return_value=None):
            rec = recorder.Recorder()
            with self.assertRaises(RuntimeError):
                rec.start()
            self.assertIsNone(rec._stream)
            stream.close.assert_called_once()
            rec.start()
            self.assertTrue(rec.recording)
            rec.stop()

    def test_stop_failure_always_closes_and_resets(self):
        rec = recorder.Recorder()
        stream = MagicMock()
        stream.stop.side_effect = RuntimeError("disconnected")
        rec._stream, rec.recording = stream, True
        with self.assertRaises(RuntimeError):
            rec.stop()
        stream.close.assert_called_once()
        self.assertFalse(rec.recording)
        self.assertIsNone(rec._stream)

    def test_monitor_stop_failure_still_closes(self):
        monitor = recorder.MicMonitor()
        stream = MagicMock()
        stream.stop.side_effect = RuntimeError
        monitor._stream = stream
        monitor.stop()
        stream.close.assert_called_once()
        self.assertIsNone(monitor._stream)


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.app = main.Mywishper.__new__(main.Mywishper)
        QObject.__init__(self.app)
        a = self.app
        a._closing = a._updating = a._loading = False
        a._lock = threading.Lock()
        a._state = main.State.IDLE
        a.recorder, a.transcriber, a.tray, a.ui = (MagicMock() for _ in range(4))
        a.recorder.recording = False
        a.recorder.start.side_effect = lambda: setattr(a.recorder, "recording", True)
        a.recorder.stop.side_effect = lambda: setattr(a.recorder, "recording", False)
        a.transcriber.is_loaded.return_value = True
        a._model_ever_ready = True
        a._last_used = time.monotonic()
        a._esc_hook = a._max_timer = None
        a.config = {"max_record_seconds": 0, "idle_release_minutes": 0, "release_on_fullscreen": True}
        self.sounds = patch.object(main, "sounds").start()
        self.addCleanup(patch.stopall)
        patch.object(main, "TempHotkey").start()

    def test_fullscreen_does_not_unload_while_recording(self):
        self.app._start_recording()
        self.assertEqual(self.app._state, main.State.RECORDING)
        with patch.object(main, "foreground_is_fullscreen", return_value=True):
            self.app._resource_poll()
        self.app.transcriber.unload.assert_not_called()
        self.app.cancel_recording()
        self.assertEqual(self.app._state, main.State.IDLE)

    def test_initial_load_failure_can_be_retried(self):
        self.app._model_ever_ready = False
        self.app.transcriber.is_loaded.return_value = False
        self.app._start_load_async = MagicMock()
        self.app.toggle()
        self.app._start_load_async.assert_called_once()
        self.app.recorder.start.assert_not_called()

    def test_extra_toggles_while_transcribing_are_ignored(self):
        self.app._state = main.State.TRANSCRIBING
        self.app.toggle()
        self.app.toggle()
        self.app.recorder.start.assert_not_called()
        self.app.recorder.stop.assert_not_called()

    def test_auto_stop_failure_recovers(self):
        self.app._state = main.State.RECORDING
        self.app.recorder.recording = True
        self.app.recorder.stop.side_effect = RuntimeError
        self.app._auto_stop()
        self.assertEqual(self.app._state, main.State.IDLE)

    def test_worker_completion_is_queued_to_gui_thread(self):
        self.app._state = main.State.TRANSCRIBING
        self.app._worker_finished.connect(self.app._finish_transcription)
        t = threading.Thread(target=self.app._worker_finished.emit)
        t.start(); t.join()
        self.assertEqual(self.app._state, main.State.TRANSCRIBING)
        qapp.processEvents()
        self.assertEqual(self.app._state, main.State.IDLE)


class PasteTest(unittest.TestCase):
    def test_native_restore_checks_sequence_inside_clipboard_lock(self):
        clipboard = winclipboard.WindowsClipboard()
        inside = []
        from contextlib import contextmanager
        @contextmanager
        def opened():
            inside.append(True)
            try:
                yield
            finally:
                inside.pop()
        def sequence():
            self.assertTrue(inside)
            return 12
        clipboard.opened = opened
        clipboard._replace = MagicMock()
        before = [(13, b"text"), (8, b"image"), (50000, b"privacy marker")]
        with patch.object(winclipboard, "_sequence", side_effect=sequence):
            self.assertFalse(clipboard.restore(before, 11))
            clipboard._replace.assert_not_called()
            self.assertTrue(clipboard.restore(before, 12))
            clipboard._replace.assert_called_once_with(before)

    def test_preserves_full_snapshot_and_skips_new_user_copy(self):
        snapshot = [(13, b"old text"), (8, b"image"), (50000, b"secret marker")]
        for user_copy in (False, True):
            clipboard = MagicMock()
            clipboard.write_text.return_value = (snapshot, 10)
            state = {"sequence": 10, "value": "dictation"}
            def restore(before, sequence):
                if sequence != state["sequence"]:
                    return False
                state["value"] = before
                return True
            clipboard.restore.side_effect = restore
            def sleep(delay):
                if delay >= 0.1 and user_copy:
                    state.update(sequence=11, value="new copy")
            with patch.object(paste, "WindowsClipboard") as factory, patch.object(paste, "_key"), patch.object(paste.time, "sleep", side_effect=sleep):
                factory.return_value.__enter__.return_value = clipboard
                paste.paste_text("dictation", True)
            self.assertEqual(state["value"], "new copy" if user_copy else snapshot)

    def test_no_restore_when_disabled(self):
        with patch.object(paste, "WindowsClipboard") as factory, patch.object(paste, "_key"), patch.object(paste.time, "sleep"):
            clipboard = factory.return_value.__enter__.return_value
            clipboard.write_text.return_value = (None, 10)
            paste.paste_text("dictation", False)
            clipboard.restore.assert_not_called()


class AsyncClipboardTest(unittest.TestCase):
    def test_clear_invalidates_copies_already_waiting_in_queue(self):
        watcher = ClipboardWatcher()
        queued = []
        real_executor = watcher._executor
        watcher._executor = MagicMock()
        watcher._executor.submit.side_effect = queued.append
        save = MagicMock(return_value=True)
        try:
            watcher._persist(save, "old queued copy")
            with patch.object(clips, "clear", return_value=True) as clear:
                self.assertTrue(watcher.clear_history())
                clear.assert_called_once()
            queued[0]()
            save.assert_not_called()
            watcher._persist(save, "new copy")
            queued[1]()
            save.assert_called_once()
        finally:
            watcher._executor = real_executor
            watcher.close()

    def test_persistence_does_not_block_gui_and_failure_allows_retry(self):
        entered, release = threading.Event(), threading.Event()
        watcher = ClipboardWatcher()
        def slow():
            entered.set()
            release.wait(2)
            return None
        try:
            watcher._last_text = "copy"
            watcher._persist(slow, "copy")
            self.assertTrue(entered.wait(1))
            self.assertFalse(release.is_set())  # the call returned while the writer waits
            release.set()
            watcher.close()
            qapp.processEvents()
            self.assertIsNone(watcher._last_text)
        finally:
            release.set()
            watcher.close()


if __name__ == "__main__":
    unittest.main()
