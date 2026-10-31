"""Clipboard UI regressions with demo data and no real clipboard writes."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
import theme
from clipui import ClipPicker, ROW_IMAGE_HEIGHT

qapp = QApplication.instance() or QApplication([])


class ClipboardWindowTest(unittest.TestCase):
    def setUp(self):
        self.pick = Mock()
        self.window = ClipPicker(theme.DARK, self.pick)
        self.addCleanup(self.window.close)

    def show_entries(self, entries):
        with patch.object(self.window, "_force_foreground"), patch.object(self.window, "_fit_screen"):
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
            self.assertGreaterEqual(self.window.list.sizeHintForRow(0), ROW_IMAGE_HEIGHT)
            self.assertEqual(self.window.list.item(0).text(), '')
            preview.actual_size()
            self.assertEqual(preview._label.pixmap().width(), 1600)

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

    def test_rows_show_content_only_and_keep_metadata_searchable(self):
        self.show_entries([{'id': 'one', 'kind': 'text', 'text': 'hello שלום', 'time': '2026-10-31 10:23'}])
        self.assertEqual(self.window.list.item(0).text(), 'hello שלום')
        self.assertFalse(self.window._detail_title.isVisible())
        self.window.search.setText('2026-10-31')
        self.window._apply_filter()
        self.assertEqual(self.window.list.count(), 1)

    def test_expansion_and_escape_restore_splitter_before_closing(self):
        self.show_entries([{'id': 'one', 'kind': 'text', 'text': 'hello'}])
        sizes = self.window.splitter.sizes()
        self.window._expand_btn.click()
        qapp.processEvents()
        self.assertFalse(self.window.list.isVisible())
        QTest.keyClick(self.window, Qt.Key_Escape)
        qapp.processEvents()
        self.assertTrue(self.window.list.isVisible())
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.splitter.sizes(), sizes)
        QTest.keyClick(self.window, Qt.Key_Escape)
        self.assertFalse(self.window.isVisible())

    def test_raise_preserves_search_selection_scroll_and_full_text(self):
        entries = [{'id': str(i), 'kind': 'text', 'text': f'hello {i}\n' * 100} for i in range(100)]
        self.show_entries(entries)
        self.window.search.setText('hello')
        self.window._apply_filter()
        self.window.list.setCurrentRow(50)
        self.window.list.scrollToItem(self.window.list.item(50))
        self.window.text_preview.verticalScrollBar().setValue(20)
        scroll = self.window.list.verticalScrollBar().value()
        with patch.object(self.window, '_force_foreground'), patch.object(self.window, '_fit_screen'):
            self.window.show_for(entries, preserve=True)
        self.assertEqual(self.window.search.text(), 'hello')
        self.assertEqual(self.window._current_id(), '50')
        self.assertEqual(self.window.list.verticalScrollBar().value(), scroll)
        self.assertEqual(self.window.text_preview.verticalScrollBar().value(), 20)

    def test_window_is_clamped_to_smaller_monitor(self):
        screen = Mock()
        screen.availableGeometry.return_value = QRect(-800, 0, 800, 600)
        self.window.resize(1200, 800)
        self.window.move(1500, 700)
        self.window._fit_screen(screen)
        self.assertTrue(screen.availableGeometry().contains(self.window.geometry()))

    def test_compact_window_keeps_image_space_and_secondary_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'demo.png'
            image = QPixmap(1600, 900)
            image.fill(QColor('#3575e5'))
            image.save(str(path))
            self.show_entries([{'id': 'one', 'kind': 'image', 'path': str(path)}])
            self.window.resize(640, 440)
            qapp.processEvents()
            self.assertGreaterEqual(self.window.image_preview.viewport().height(), 120)
            self.assertTrue(self.window._options_btn.isVisible())
            self.window._refresh_menu()
            self.assertTrue(self.window._menu_delete.isEnabled())
            self.assertTrue(self.window._menu_pause.isEnabled())
            self.window._menu_pause.trigger()
            self.window.resize(1000, 680)
            qapp.processEvents()
            self.assertTrue(self.window._secondary_actions.isVisible())

    def test_maximized_minimized_window_restores_then_can_return_to_normal(self):
        self.show_entries([])
        normal = self.window.geometry()
        self.window.showMaximized()
        qapp.processEvents()
        self.window.showMinimized()
        qapp.processEvents()
        self.show_entries([])
        self.assertTrue(self.window.isMaximized())
        self.window.showNormal()
        qapp.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(self.window.geometry(), normal)

    def test_activation_retries_once_and_alerts_on_denial(self):
        self.show_entries([])
        with patch('platform_support.foreground_context', return_value=(123, 7)), \
             patch('platform_support.bring_forward', return_value=False) as activate, \
             patch.object(self.window, 'is_foreground', return_value=False), \
             patch.object(QApplication, 'alert') as alert:
            self.window._force_foreground()
            QTest.qWait(160)
        self.assertEqual(activate.call_count, 2)
        alert.assert_called_once_with(self.window)
        self.assertFalse(self.window._activation_timer.isActive())

    def test_activation_retry_is_cancelled_by_input_or_close(self):
        for close in (False, True):
            self.show_entries([])
            with patch('platform_support.foreground_context', side_effect=[(123, 7), (456, 8)]), \
                 patch('platform_support.bring_forward', return_value=False) as activate, \
                 patch.object(self.window, 'is_foreground', return_value=False):
                self.window._force_foreground()
                if close:
                    self.window.close()
                QTest.qWait(160)
            activate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
