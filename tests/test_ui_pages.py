"""Render all extracted pages and exercise asynchronous settings without backends."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFontDatabase
from config import DEFAULTS
from ui import AppUI, MainWindow
import theme

qapp = QApplication.instance() or QApplication([])
# Offscreen's font database can be empty even when Windows has Hebrew fonts.
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    for font in ("segoeui.ttf", "segoeuib.ttf", "arial.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))


def controller(name="dark"):
    return AppUI(dict(DEFAULTS, theme=name), lambda: 0, lambda c: True,
                 lambda: [{"id": "sample", "time": "2026-09-06 10:00", "text": "בדיקת עברית עם Windows"}],
                 lambda: True, lambda s: None, lambda *a: None,
                 list_corrections=lambda: {"טעות": "תיקון"},
                 english_terms=lambda: ["Windows", "WhatsApp"])


class PagesTest(unittest.TestCase):
    def settings(self, name="light"):
        ui = controller(name)
        ui._win = window = MainWindow(ui, theme.palette(name))
        def cleanup():
            window._force_close = True
            window.close()
            ui._overlay.close()
        self.addCleanup(cleanup)
        window.show()
        window._goto(2, from_nav=False)
        qapp.processEvents()
        return ui, window

    def test_settings_first_view_and_keyboard_disclosures(self):
        ui, w = self.settings()
        scroll = w.stack.widget(2)
        for size in ((720, 560), (900, 680)):
            w.resize(*size)
            qapp.processEvents()
            for widget in (w._hk_change, w._mic_test_btn):
                self.assertTrue(scroll.viewport().rect().contains(widget.mapTo(scroll.viewport(), widget.rect().center())))
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertFalse(w._hk_edit.isVisible())
        self.assertFalse(w._mic_level.isVisible())
        self.assertFalse(w._upd_btn.isVisible())
        w.resize(720, 560)
        w._help_panel.header.setFocus()
        QTest.keyClick(w._help_panel.header, Qt.Key_Space)
        qapp.processEvents()
        self.assertTrue(w._upd_btn.isVisible())
        # Mac has fewer sections and different font metrics. Create overflow
        # deliberately before testing restoration of a nonzero scroll offset.
        w._hk_change.click()
        w._technical.set_open(True)
        w._upd_btn.setFocus()
        qapp.processEvents()
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        offset = scroll.verticalScrollBar().value()
        self.assertGreater(offset, 0)
        ui.set_theme("dark")
        qapp.processEvents()
        self.assertTrue(w._help_panel.header.isChecked())
        self.assertTrue(w._upd_btn.hasFocus())
        self.assertEqual(scroll.verticalScrollBar().value(), offset)

    def test_shortcut_capture_cancel_rejection_and_success(self):
        ui, w = self.settings()
        ui.set_hotkey = Mock(return_value=False)
        w._hk_change.click()
        w._hk_edit.click()
        QTest.keyClick(w._hk_edit, Qt.Key_Escape)
        self.assertTrue(w.isVisible())
        ui.set_hotkey.assert_not_called()
        with patch.object(QMessageBox, "warning") as warn:
            w._hk_edit.click()
            QTest.keyClick(w._hk_edit, Qt.Key_F9)
            warn.assert_called_once()
        self.assertEqual(w._hk_edit.text(), ui.config["hotkey"])
        ui.set_hotkey.return_value = True
        w._hk_edit.click()
        QTest.keyClick(w._hk_edit, Qt.Key_F9)
        self.assertEqual(w._hk_label.text(), "F9")
        self.assertFalse(w._hk_edit.isVisible())

    def test_missing_mic_is_retained_and_meter_lives_only_while_testing(self):
        ui, w = self.settings()
        ui.config["input_device"] = "Unplugged"
        ui.list_input_devices = lambda: ["USB mic"]
        ui.set_input_device = Mock()
        w._populate_mics()
        self.assertEqual(w._mic_combo.currentData(), "Unplugged")
        self.assertTrue(w._mic_warning.isVisible())
        ui.set_input_device.assert_not_called()
        w._mic_combo.setCurrentIndex(w._mic_combo.findData("USB mic"))
        self.assertFalse(w._mic_warning.isVisible())
        ui.mic_test_start = Mock(return_value=True)
        ui.mic_test_stop = Mock()
        ui.mic_level = lambda: 0.4
        w._mic_test_btn.click()
        ui.mic_test_start.assert_called_once_with("USB mic")
        w._update_mic_level()
        self.assertTrue(w._mic_status.isVisible())
        self.assertEqual(w._mic_level.value(), 40)
        w._goto(0)
        ui.mic_test_stop.assert_called_once()
        w._goto(2)
        self.assertFalse(w._mic_status.isVisible())
        self.assertFalse(w._mic_level.isVisible())

    def test_status_help_opens_details_or_platform_setup(self):
        ui, w = self.settings()
        ui.model_status = lambda: {"state": "error", "model": "demo", "device": "cpu"}
        ui.open_mac_setup = Mock()
        for mac in (False, True):
            with patch("ui_pages.settings_page.IS_MAC", mac):
                w._refresh_model_status()
                self.assertTrue(w._status_help.isVisible())
                w._status_help.click()
                if mac:
                    ui.open_mac_setup.assert_called_once()
                else:
                    self.assertTrue(w._status_sub.isVisible())
                    self.assertIn("demo", w._status_sub.text())

    def test_failed_dictionary_save_keeps_input_for_retry(self):
        ui = controller()
        ui.add_correction = lambda *a: False
        window = MainWindow(ui, theme.DARK)
        try:
            window._corr_wrong.setText("טעות")
            window._corr_right.setText("תיקון")
            window._add_corr_manual()
            self.assertEqual(window._corr_wrong.text(), "טעות")
            self.assertEqual(window._corr_right.text(), "תיקון")
        finally:
            window._force_close = True
            window.close()
            ui._overlay.close()

    def test_all_pages_render_in_both_themes(self):
        for name in ("dark", "light"):
            ui = controller(name)
            window = MainWindow(ui, theme.palette(name))
            try:
                window.show()
                for page in range(3):
                    window._goto(page, from_nav=False)
                    qapp.processEvents()
                    self.assertEqual(window.stack.currentIndex(), page)
                    self.assertFalse(window.grab().isNull())
                window.search.setText("Windows")
                window.refresh_history()
                self.assertEqual(len(window._entries), 1)
            finally:
                window._force_close = True
                window.close()
                ui._overlay.close()

    def test_theme_changes_keep_native_window_search_and_draft(self):
        ui = controller()
        ui._win = window = MainWindow(ui, theme.DARK)
        try:
            window.show()
            window.search.setText("Windows")
            window._corr_wrong.setText("טיוטה")
            window._goto(1, from_nav=False)
            qapp.processEvents()
            handle = int(window.winId())
            geometry = window.geometry()
            for name in ("light", "dark", "light"):
                ui.set_theme(name)
                qapp.processEvents()
                self.assertIs(ui._win, window)
                self.assertEqual(int(window.winId()), handle)
                self.assertTrue(window.isVisible())
                self.assertEqual(window.geometry(), geometry)
                self.assertEqual(window.stack.currentIndex(), 1)
                self.assertEqual(window.search.text(), "Windows")
                self.assertEqual(window._corr_wrong.text(), "טיוטה")
                self.assertEqual(window._theme_sw._p["name"], name)
        finally:
            window._force_close = True
            window.close()
            ui._overlay.close()

    def test_settings_have_no_ollama_controls_and_window_can_grow(self):
        ui = controller()
        window = MainWindow(ui, theme.DARK)
        try:
            self.assertFalse(hasattr(window, "_llm_combo"))
            window.resize(1200, 850)
            self.assertEqual(window.width(), 1200)
            self.assertEqual(window.height(), 850)
        finally:
            window._force_close = True
            window.close()
            ui._overlay.close()


if __name__ == "__main__":
    unittest.main()
