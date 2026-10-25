"""Clipboard UI regressions with demo data and no real clipboard writes."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
import theme
from clipui import ClipPicker

qapp = QApplication.instance() or QApplication([])


class ClipboardWindowTest(unittest.TestCase):
    def setUp(self):
        self.pick = Mock()
        self.window = ClipPicker(theme.DARK, self.pick)
        self.addCleanup(self.window.close)

    def show_entries(self, entries):
        with patch.object(self.window, "_force_foreground"):
            self.window.show_for(entries)
        qapp.processEvents()

    def test_long_text_is_complete_and_scrollable_without_copying(self):
        text = ("עברית Windows טקסט ארוך\n" * 1000) + "END OF TEXT"
        self.show_entries([{"id": "demo", "kind": "text", "text": text}])
        self.assertEqual(self.window.text_preview.toPlainText(), text)
        self.assertGreater(self.window.text_preview.verticalScrollBar().maximum(), 0)
        self.assertTrue(self.window.isVisible())
        self.pick.assert_not_called()
        self.window._copy_selected()
        self.pick.assert_called_once()
        self.assertFalse(self.window.isVisible())

    def test_large_image_has_fit_and_zoom_and_no_stale_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "demo.png"
            image = QPixmap(1600, 900)
            image.fill(QColor("#2F6FE0"))
            image.save(str(path))
            self.show_entries([{"id": "image", "kind": "image", "path": str(path)}])
            preview = self.window.image_preview
            self.assertTrue(preview.isVisible())
            self.assertGreater(preview._label.pixmap().width(), 200)
            fit_width = preview._label.pixmap().width()
            preview.zoom(1.25)
            self.assertGreater(preview._label.pixmap().width(), fit_width)
            preview.fit()
            self.assertIsNone(preview._zoom)
            self.assertFalse(self.window.text_preview.isVisible())
            self.assertEqual(self.window.list.iconSize().width(), 64)

    def test_escape_closes_and_size_survives_reopen(self):
        self.show_entries([])
        self.window.resize(1200, 800)
        QTest.keyClick(self.window, Qt.Key_Escape)
        self.assertFalse(self.window.isVisible())
        self.show_entries([])
        self.assertEqual(self.window.size().width(), 1200)
        self.assertEqual(self.window.size().height(), 800)

    def test_bulk_delete_cancellation_preserves_entries(self):
        self.show_entries([{"id": "demo", "kind": "text", "text": "keep"}])
        clear = self.window._on_clear = Mock()
        with patch.object(QMessageBox, "exec"), patch.object(QMessageBox, "clickedButton", return_value=None):
            self.window._clear_all()
        clear.assert_not_called()
        self.assertEqual(self.window.list.count(), 1)

    def test_theme_change_keeps_picker_handle_and_selection(self):
        self.show_entries([{"id": "demo", "kind": "text", "text": "keep"}])
        handle = int(self.window.winId())
        self.window.set_palette(theme.LIGHT)
        self.assertEqual(int(self.window.winId()), handle)
        self.assertEqual(self.window._current_id(), "demo")
        self.assertIn(theme.LIGHT["surface"], self.window.list.styleSheet())

    def test_failed_copy_keeps_window_and_shows_error(self):
        self.show_entries([{'id':'one', 'kind':'text', 'text':'one'}])
        self.pick.return_value = False
        self.window._copy_selected()
        self.assertTrue(self.window.isVisible())
        self.assertIn('לא הצליחה', self.window._feedback.text())

    def test_type_filter_clears_stale_search_and_reveals_images(self):
        self.show_entries([{'id':'text', 'kind':'text', 'text':'one'},
                           {'id':'image', 'kind':'image', 'path':'missing.png'}])
        self.window.search.setText('one')
        self.window._apply_filter()
        self.window._set_filter('image')
        self.assertEqual(self.window.search.text(), '')
        self.assertEqual(self.window.list.count(), 1)
        self.assertTrue(self.window._gallery_btn.isChecked())
        self.assertFalse(self.window._copy_btn.isEnabled())

    def test_large_history_is_paged_without_losing_selection(self):
        self.show_entries([{'id':str(i),'kind':'text','text':str(i)} for i in range(300)])
        self.assertEqual(self.window.list.count(), 100)
        self.window.list.setCurrentRow(50)
        self.window._show_more()
        self.assertEqual(self.window.list.count(), 200)
        self.assertEqual(self.window._current_id(), '50')

    def test_delete_and_undo_and_pin(self):
        entry = {'id':'one','kind':'text','text':'one'}
        self.window._on_delete = Mock(return_value=(entry,0))
        self.window._on_restore = Mock(return_value=True)
        self.window._on_pin = Mock(return_value=True)
        self.show_entries([entry])
        self.window._toggle_pin()
        self.assertTrue(entry['pinned'])
        self.window._delete_selected()
        self.assertEqual(self.window.list.count(), 0)
        self.window._undo_delete()
        self.assertEqual(self.window.list.count(), 1)
        self.assertIsNone(self.window._undo)

    def test_delete_in_search_does_not_delete_clip(self):
        self.window._on_delete = Mock()
        self.show_entries([{'id':'one','kind':'text','text':'one'}])
        self.window.search.setText('one')
        self.window.search.setCursorPosition(0)
        QTest.keyClick(self.window.search, Qt.Key_Delete)
        self.window._on_delete.assert_not_called()

    def test_pause_control_uses_callback_and_displays_state(self):
        state = [False]
        self.window._is_paused = lambda: state[0]
        self.window._on_pause = lambda value: state.__setitem__(0,value)
        self.window._toggle_pause()
        self.assertTrue(state[0])
        self.assertIn('מושהית', self.window._capture_status.text())


if __name__ == "__main__":
    unittest.main()
