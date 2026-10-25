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

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage, QColor
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
        self.w = ClipboardWatcher(on_change=lambda: self.changed.append(1), persist_async=False)
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
            "image": lambda _self: md.imageData(),
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

    def test_screenshot_with_windows_allow_flag_is_saved(self):
        md = QMimeData()
        image = QImage(80, 60, QImage.Format_ARGB32)
        image.fill(QColor("blue"))
        md.setImageData(image)
        md.setData('application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"', b'\x01\0\0\0')
        # A cloud-sync exclusion is not a local-history exclusion.
        md.setData('application/x-qt-windows-mime;value="CanUploadToCloudClipboard"', b'\0\0\0\0')
        self._feed(md)
        saved = clips.load()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["kind"], "image")
        self.assertFalse(QImage(saved[0]["path"]).isNull())

    def test_allow_history_does_not_override_explicit_secret(self):
        for fmt in ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore",
                    "CanCloudSyncClipboard", "org.nspasteboard.ConcealedType"):
            md = _mime("private", fmt)
            md.setData("CanIncludeInClipboardHistory", b'\x01\0\0\0')
            self._feed(md)
        self.assertEqual(clips.load(), [])

    def test_deny_or_malformed_history_flag_is_not_saved(self):
        for value in (b'\0\0\0\0', b'', b'1', b'\x01', b'\x02\0\0\0', b'\x01\0\0\0\0'):
            md = _mime("private")
            md.setData("CanIncludeInClipboardHistory", value)
            self._feed(md)
        self.assertEqual(clips.load(), [])

    def test_image_beats_accompanying_text_and_duplicate_is_merged(self):
        md = _mime("https://example.test/image.png")
        image = QImage(20, 20, QImage.Format_RGB32)
        image.fill(QColor("green"))
        md.setImageData(image)
        self._feed(md)
        self._feed(md)
        self.assertEqual([e['kind'] for e in clips.load()], ['image'])
        self.assertEqual(len(list(clips.IMAGE_DIR.glob('*.png'))), 1)

    def test_local_image_file_is_snapshotted(self):
        path = Path(self._tmp.name) / 'source.png'
        image = QImage(20, 20, QImage.Format_RGB32)
        image.fill(QColor("green"))
        image.save(str(path))
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(path))])
        self._feed(md)
        saved = clips.load()[0]
        path.unlink()
        self.assertEqual(saved['name'], 'source.png')
        self.assertTrue(Path(saved['path']).exists())

    def test_text_can_be_recopied_after_an_image(self):
        self._feed(_mime("text"))
        md = QMimeData()
        image = QImage(10, 10, QImage.Format_RGB32)
        image.fill(QColor("red"))
        md.setImageData(image)
        self._feed(md)
        self._feed(_mime("text"))
        self.assertEqual([e['kind'] for e in clips.load()], ['text', 'image'])


if __name__ == "__main__":
    unittest.main()
