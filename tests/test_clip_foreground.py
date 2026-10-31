"""Clipboard shortcut routing and pointer-sized Windows activation contracts."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import platform_support


class ClipboardToggleTest(TestCase):
    def test_only_foreground_picker_is_toggled_closed(self):
        import main
        for active, visible in ((True, True), (False, True), (False, False)):
            picker = Mock()
            picker.is_foreground.return_value = active
            picker.isVisible.return_value = visible
            owner = SimpleNamespace(_clip_picker=picker)
            with patch.object(main.clips, "load", return_value=[]) as load:
                main.Mywishper.show_clips(owner)
            if active:
                picker.hide.assert_called_once()
                load.assert_not_called()
            else:
                picker.hide.assert_not_called()
                picker.show_for.assert_called_once_with([], preserve=visible)


class WindowsActivationTest(TestCase):
    def test_restore_and_verify_without_truncating_handle(self):
        from PySide6.QtWidgets import QApplication
        hwnd = 0x123456789
        window = Mock()
        window.winId.return_value = hwnd
        window.isVisible.return_value = True
        window.isMinimized.return_value = False
        api = Mock()
        api.IsIconic.return_value = True
        with patch.object(platform_support, 'IS_MAC', False), \
             patch.object(platform_support, '_window_api', return_value=api), \
             patch.object(platform_support, 'foreground_context', return_value=(hwnd, 0)), \
             patch.object(QApplication, 'platformName', return_value='windows'):
            self.assertTrue(platform_support.bring_forward(window))
        api.ShowWindow.assert_called_once_with(hwnd, 9)
        api.SetForegroundWindow.assert_called_once_with(hwnd)

    def test_declined_activation_returns_false(self):
        from PySide6.QtWidgets import QApplication
        window = Mock()
        window.winId.return_value = 100
        window.isVisible.return_value = True
        window.isMinimized.return_value = False
        with patch.object(platform_support, 'IS_MAC', False), \
             patch.object(platform_support, '_window_api', return_value=Mock()), \
             patch.object(platform_support, 'foreground_context', return_value=(200, 0)), \
             patch.object(QApplication, 'platformName', return_value='windows'):
            self.assertFalse(platform_support.bring_forward(window))
