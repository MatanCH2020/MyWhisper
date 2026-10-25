"""Native bridge contracts using fake pasteboards/AX; never read the real clipboard."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


@unittest.skipUnless(sys.platform == "darwin", "macOS native bridges")
class NativeClipboardTest(unittest.TestCase):
    def setUp(self):
        import macos_clipboard
        self.module = macos_clipboard
        self.item = Mock()
        self.item.types.return_value = ["public.utf8-plain-text"]
        self.item.dataForType_.return_value = b"synthetic"
        self.board = Mock()
        self.board.types.return_value = ["public.utf8-plain-text"]
        self.board.pasteboardItems.return_value = [self.item]
        self.board.changeCount.return_value = 10
        self.board.accessBehavior.return_value = 2

    def test_concealed_payload_is_not_read(self):
        self.board.types.return_value += ["org.nspasteboard.ConcealedType"]
        self.assertIsNone(self.module.safe_snapshot(self.board))
        self.item.dataForType_.assert_not_called()

    def test_marker_on_second_item_blocks_all_payloads(self):
        second = Mock()
        second.types.return_value = ["org.nspasteboard.TransientType"]
        self.board.pasteboardItems.return_value = [self.item, second]
        self.assertIsNone(self.module.safe_snapshot(self.board))
        self.item.dataForType_.assert_not_called()
        second.dataForType_.assert_not_called()

    def test_denial_or_ask_never_triggers_polling_reads(self):
        for status in (0, 1, 3):
            self.board.accessBehavior.return_value = status
            self.assertIsNone(self.module.safe_snapshot(self.board))
        self.board.types.assert_not_called()
        self.item.dataForType_.assert_not_called()

    def test_snapshot_discards_racing_copy(self):
        self.board.changeCount.side_effect = [10, 11]
        self.assertIsNone(self.module.safe_snapshot(self.board))

    def test_restore_never_overwrites_a_new_copy(self):
        self.board.changeCount.return_value = 11
        self.assertFalse(self.module._restore(self.board, [{"public.utf8-plain-text": b"old"}], 10))
        self.board.clearContents.assert_not_called()

    def test_safe_snapshot_preserves_exact_bytes(self):
        self.assertEqual(self.module.safe_snapshot(self.board, preserve=True),
                         ([{"public.utf8-plain-text": b"synthetic"}], 10))

    def test_history_permission_marker_is_checked_before_text(self):
        self.item.types.return_value = ["public.utf8-plain-text", "CanIncludeInClipboardHistory"]
        for value in (b"\x00\x00\x00\x00", b"\x01", None):
            self.item.dataForType_.reset_mock()
            self.item.dataForType_.side_effect = lambda kind: value if kind == "CanIncludeInClipboardHistory" else b"secret"
            self.assertIsNone(self.module.safe_snapshot(self.board))
            self.item.dataForType_.assert_called_once_with("CanIncludeInClipboardHistory")

    def test_missing_destination_does_not_touch_board(self):
        with patch.object(self.module, "on_main", side_effect=lambda fn: fn()), \
             patch.object(self.module, "accessibility_allowed", return_value=True), \
             patch.object(self.module.AppKit, "NSPasteboard") as board:
            self.assertFalse(self.module.paste_text("synthetic", destination_check=lambda: False))
        board.generalPasteboard.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "macOS native bridges")
class NativeDestinationTest(unittest.TestCase):
    def test_transition_away_and_back_stays_blocked(self):
        import macos_system as system
        target = object()
        with patch.object(system, "foreground_window", return_value=target), \
             patch.object(system, "same_destination", return_value=True):
            guard = system.DestinationGuard()
            guard._ready = True
            self.assertTrue(guard.unchanged())
            guard.changed.set()
            self.assertFalse(guard.unchanged())

    def test_observer_failure_is_fail_closed(self):
        import macos_system as system
        target = Mock(pid=123)
        with patch.object(system, "foreground_window", return_value=target), \
             patch.object(system.AX, "AXUIElementCreateApplication", return_value=object()), \
             patch.object(system.AX, "AXObserverCreate", return_value=(-1, None)):
            guard = system.DestinationGuard().start()
            self.assertFalse(guard.unchanged())

    def test_revoked_accessibility_prevents_destination_identification(self):
        import macos_system as system
        with patch.object(system, "accessibility_allowed", return_value=False), \
             patch.object(system.AppKit, "NSWorkspace") as workspace:
            self.assertIsNone(system._foreground())
        workspace.sharedWorkspace.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "Mac audio dependencies")
class NativeAudioTest(unittest.TestCase):
    def test_resampling_preserves_duration_and_tone(self):
        import numpy as np
        from recorder import resample_capture
        signal = np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000).astype(np.float32)
        converted = resample_capture(signal, 48000)
        self.assertEqual(converted.dtype, np.float32)
        self.assertEqual(len(converted), 16000)
        self.assertEqual(np.argmax(np.abs(np.fft.rfft(converted))), 1000)

    def test_native_rate_fallback_does_not_switch_device(self):
        import recorder
        with patch.object(recorder.sd, "check_input_settings", side_effect=recorder.sd.PortAudioError("rate")), \
             patch.object(recorder.sd, "query_devices", return_value={"default_samplerate": 48000}) as query:
            self.assertEqual(recorder.capture_rate(7), 48000)
        query.assert_called_once_with(7, "input")
