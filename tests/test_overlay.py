"""HUD placement and lifecycle regressions; no microphone or GPU required."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QAbstractAnimation, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFontDatabase, QFontMetricsF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import overlay

qapp = QApplication.instance() or QApplication([])
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    for name in ("segoeui.ttf", "segoeuib.ttf", "arial.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))


def screen(rect):
    result = Mock()
    result.availableGeometry.return_value = rect
    return result


class PlacementTest(unittest.TestCase):
    def test_monitor_origins_and_logical_dpi_geometry(self):
        for rect in (QRect(0, 0, 1920, 1040), QRect(-1920, 0, 1920, 1040),
                     QRect(1920, -200, 1707, 920), QRect(0, -1440, 2560, 1400)):
            with self.subTest(rect=rect):
                size = QSize(overlay.Overlay.CARD_W + 2 * overlay.Overlay.SHADOW,
                             overlay.Overlay.CARD_H + 2 * overlay.Overlay.SHADOW)
                pos = overlay.hud_position(rect, size)
                self.assertEqual(pos.x(), rect.x() + (rect.width() - size.width()) // 2)
                self.assertEqual(pos.y(), rect.y() + 32)
                self.assertTrue(rect.contains(QRect(pos, size)))


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.hud = overlay.Overlay(lambda: 0.8)
        self.left = screen(QRect(-1920, 0, 1920, 1040))
        self.right = screen(QRect(0, 0, 2560, 1400))
        self.choose = patch.object(self.hud, "_choose_screen", return_value=self.left).start()
        patch.object(overlay, "motion_enabled", return_value=False).start()

    def tearDown(self):
        self.hud.close()
        self.hud.deleteLater()
        qapp.processEvents()
        patch.stopall()

    def test_anchor_survives_mouse_move_and_transcribing(self):
        self.hud.set_state("recording")
        position = self.hud.pos()
        self.choose.return_value = self.right
        self.hud.set_state("recording")
        self.hud.set_state("transcribing")
        self.assertEqual(self.hud.pos(), position)
        self.assertEqual(self.choose.call_count, 1)
        self.hud.set_state("idle")
        self.hud.set_state("recording")
        self.assertEqual(self.hud._screen, self.right)
        self.assertGreaterEqual(self.hud.x(), 0)

    def test_unplug_and_geometry_change(self):
        self.hud.set_state("recording")
        self.choose.return_value = self.right
        self.hud._screen_removed(self.left)
        self.assertEqual(self.hud._screen, self.right)
        self.assertEqual(self.hud.pos(), QPoint((2560 - self.hud.width()) // 2, 32))
        self.right.availableGeometry.return_value = QRect(0, 40, 1280, 680)
        self.hud._reposition()
        self.assertEqual(self.hud.pos(), QPoint((1280 - self.hud.width()) // 2, 72))

    def test_rendering_is_independent_of_global_rtl(self):
        original = qapp.layoutDirection()
        try:
            for state in ("recording", "transcribing"):
                self.hud.state = state
                self.hud._level = 1.0
                for elapsed in (0, 599, 600, 5999):
                    self.hud._elapsed = elapsed
                    images = []
                    for direction in (Qt.LeftToRight, Qt.RightToLeft):
                        qapp.setLayoutDirection(direction)
                        images.append(self.hud.grab().toImage())
                    with self.subTest(state=state, elapsed=elapsed):
                        self.assertEqual(images[0], images[1])
        finally:
            qapp.setLayoutDirection(original)

    def test_painted_text_fits_separate_regions(self):
        # Exercise the actual paint path and actual font metrics; catch clipping,
        # overlapping regions and lost minimum spacing in both HUD states.
        original = self.hud._text
        for state in ("recording", "transcribing"):
            for elapsed in (0, 599, 600, 5999):
                regions = []

                def measure(painter, rect, text, *args, **kwargs):
                    original(painter, rect, text, *args, **kwargs)
                    metrics = QFontMetricsF(painter.font())
                    self.assertLessEqual(metrics.horizontalAdvance(text), rect.width())
                    self.assertLessEqual(metrics.height(), rect.height())
                    regions.append(rect)

                self.hud.state = state
                self.hud._elapsed = elapsed
                with patch.object(self.hud, "_text", side_effect=measure):
                    self.hud.grab()
                self.assertEqual(len(regions), 3)
                title, clock, hint = regions
                self.assertGreaterEqual(title.left() - clock.right(), 12)
                self.assertGreaterEqual(hint.top() - title.bottom(), 12)

    def test_reduced_motion_and_nonactivation(self):
        self.hud.set_state("recording")
        self.assertEqual(self.hud._entrance.state(), QAbstractAnimation.Stopped)
        self.assertEqual(self.hud.windowOpacity(), 1.0)
        self.assertTrue(self.hud.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertTrue(self.hud.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(self.hud.windowFlags() & Qt.WindowTransparentForInput)

    def test_cancel_during_entrance_and_restart(self):
        with patch.object(overlay, "motion_enabled", return_value=True):
            self.hud.set_state("recording")
            self.hud.set_state("transcribing")
            self.hud.set_state("idle")
            self.assertFalse(self.hud.isVisible())
            self.assertFalse(self.hud._timer.isActive())
            self.assertEqual(self.hud._entrance.state(), QAbstractAnimation.Stopped)
            QTest.qWait(240)
            self.assertFalse(self.hud.isVisible())
            self.hud.set_state("recording")
            QTest.qWait(260)
            self.assertTrue(self.hud.isVisible())
            self.assertAlmostEqual(self.hud.windowOpacity(), 1.0)
            self.assertEqual(self.hud.pos(), overlay.hud_position(
                self.left.availableGeometry(), self.hud.size()))

    def test_clock_freezes_during_transcription_resets_for_next_recording(self):
        with patch.object(overlay.time, "monotonic", return_value=100):
            self.hud.set_state("recording")
        with patch.object(overlay.time, "monotonic", return_value=165):
            self.hud._tick()
        self.assertEqual(self.hud._elapsed, 65)
        self.hud.set_state("transcribing")
        self.hud._tick()
        self.assertEqual(self.hud._elapsed, 65)
        self.hud.set_state("idle")
        self.hud.set_state("recording")
        self.assertEqual(self.hud._elapsed, 0)

    def test_close_stops_timer(self):
        self.hud.set_state("recording")
        self.hud.close()
        self.assertFalse(self.hud._timer.isActive())

    def test_no_cursor_screen_falls_back_to_primary(self):
        with patch.object(overlay, "QApplication") as app:
            app.screenAt.return_value = None
            app.primaryScreen.return_value = self.right
            self.assertEqual(overlay.Overlay._choose_screen(self.hud), self.right)


if __name__ == "__main__":
    unittest.main()
