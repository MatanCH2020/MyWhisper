"""A failed native foreground monitor must never authorize automatic paste."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import delivery


class DeliveryGuardTest(unittest.TestCase):
    def test_missing_hook_blocks_even_if_same_window(self):
        with patch.object(delivery, 'foreground_window', return_value=123), \
             patch.object(delivery.ctypes, 'WinDLL') as dll:
            dll.return_value.SetWinEventHook.return_value = 0
            guard = delivery.DestinationGuard().start()
            self.assertFalse(guard.unchanged())

    def test_hook_exception_blocks_paste(self):
        with patch.object(delivery, 'foreground_window', return_value=123), \
             patch.object(delivery.ctypes, 'WinDLL', side_effect=OSError):
            guard = delivery.DestinationGuard().start()
            self.assertFalse(guard.unchanged())

    def test_unstarted_or_closed_guard_never_authorizes(self):
        with patch.object(delivery, 'foreground_window', return_value=123):
            guard = delivery.DestinationGuard()
            self.assertFalse(guard.unchanged())

    def test_window_switch_invalidates_working_hook(self):
        with patch.object(delivery, 'foreground_window', return_value=123), \
             patch.object(delivery.ctypes, 'WinDLL') as dll:
            dll.return_value.SetWinEventHook.return_value = 1
            guard = delivery.DestinationGuard().start()
            self.assertTrue(guard.unchanged())
            guard._callback(1, 3, 456, 0, 0, 0, 0)
            self.assertFalse(guard.unchanged())
            guard.close()
