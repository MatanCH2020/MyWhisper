"""Render all extracted pages and exercise asynchronous settings without backends."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtWidgets import QApplication
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
