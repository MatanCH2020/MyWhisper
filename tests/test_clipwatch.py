"""Tests for the clipboard watcher — above all, that it does NOT capture secrets.

A clipboard manager that stores password-manager output is a password logger,
so the skip path gets more coverage here than the happy path.

Runs headless (QT_QPA_PLATFORM=offscreen).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication

import clips
from clipwatch import ClipboardWatcher

_app = QApplication.instance() or QApplication([])


def _mime(text=None, secret_format=None):
    md = QMimeData()
    if text is not None:
        md.setText(text)
    if secret_format is not None:
        md.setData(secret_format, b"1")
    return md


class ClipboardWatcherTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig = (clips.CLIPS_PATH, clips.IMAGE_DIR)
        clips.CLIPS_PATH = root / "clips.json"
        clips.IMAGE_DIR = root / "clip_images"
        self.changed = []
        self.w = ClipboardWatcher(on_change=lambda: self.changed.append(1))
        self.w._last_text = None  # ignore whatever the real clipboard held

    def tearDown(self):
        try:
            QApplication.clipboard().dataChanged.disconnect(self.w._on_data_changed)
        except (RuntimeError, TypeError):
            pass
        clips.CLIPS_PATH, clips.IMAGE_DIR = self._orig
        self._tmp.cleanup()

    def _feed(self, md):
        """Drive the handler with a given mime payload, bypassing the OS."""
        self.w._clip = type("FakeClip", (), {
            "mimeData": lambda _self: md,
            "text": lambda _self: md.text(),
            "image": lambda _self: None,
        })()
        self.w._on_data_changed()

    # ---- the security-critical path ----
    def test_password_manager_formats_are_never_stored(self):
        for fmt in ("ExcludeClipboardContentFromMonitorProcessing",
                    "Clipboard Viewer Ignore",
                    "CanIncludeInClipboardHistory",
                    "CanCloudSyncClipboard",
                    "org.nspasteboard.ConcealedType"):
            with self.subTest(fmt=fmt):
                self._feed(_mime("hunter2-secret", secret_format=fmt))
                self.assertEqual(
                    clips.load(), [],
                    f"secret marked with {fmt!r} must not be stored")

    def test_secret_format_matching_is_case_insensitive(self):
        self._feed(_mime("s3cret", secret_format="CLIPBOARD VIEWER IGNORE"))
        self.assertEqual(clips.load(), [])

    def test_ordinary_text_is_stored(self):
        self._feed(_mime("טקסט רגיל"))
        self.assertEqual([e["text"] for e in clips.load()], ["טקסט רגיל"])
        self.assertEqual(len(self.changed), 1)

    # ---- pause ----
    def test_paused_watcher_stores_nothing(self):
        self.w.set_paused(True)
        self._feed(_mime("בזמן השהיה"))
        self.assertEqual(clips.load(), [])

    def test_resuming_starts_storing_again(self):
        self.w.set_paused(True)
        self._feed(_mime("מושהה"))
        self.w.set_paused(False)
        self._feed(_mime("פעיל"))
        self.assertEqual([e["text"] for e in clips.load()], ["פעיל"])

    def test_is_paused_reports_state(self):
        self.assertFalse(self.w.is_paused())
        self.w.set_paused(True)
        self.assertTrue(self.w.is_paused())

    # ---- suppression around our own writes ----
    def test_suppressed_window_ignores_changes(self):
        self.w.suppress(5.0)
        self._feed(_mime("תמלול שהודבק"))
        self.assertEqual(clips.load(), [])

    def test_capture_resumes_after_suppression_expires(self):
        self.w.suppress(-1)  # already expired
        self._feed(_mime("אחרי"))
        self.assertEqual(len(clips.load()), 1)

    def test_suppress_takes_the_longest_window(self):
        self.w.suppress(5.0)
        self.w.suppress(0.01)  # must not shorten the active window
        self._feed(_mime("עדיין מדוכא"))
        self.assertEqual(clips.load(), [])

    # ---- noise filtering ----
    def test_same_text_announced_twice_is_stored_once(self):
        self._feed(_mime("כפול"))
        self._feed(_mime("כפול"))
        self.assertEqual(len(clips.load()), 1)

    def test_blank_text_is_ignored(self):
        self._feed(_mime("   "))
        self.assertEqual(clips.load(), [])

    def test_empty_mimedata_is_ignored(self):
        self._feed(QMimeData())
        self.assertEqual(clips.load(), [])

    def test_none_mimedata_does_not_raise(self):
        self._feed(None)  # must not blow up inside the Qt signal handler
        self.assertEqual(clips.load(), [])


if __name__ == "__main__":
    unittest.main()
