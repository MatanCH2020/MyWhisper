"""Unit tests for the shared Qt widgets.

Runs headless (QT_QPA_PLATFORM=offscreen) so it needs no display.

Run from the project root:
    .\\.venv\\Scripts\\python -m unittest discover tests
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import theme
from widgets import NavRail, ToggleSwitch

_app = QApplication.instance() or QApplication([])


class ToggleSwitchTestCase(unittest.TestCase):
    """The switch paints its own 46x26 track and has no label, so QCheckBox's
    default hit area was a ~14x14 corner square — clicks on the rest of the
    visible control did nothing. It must be clickable edge to edge."""

    def setUp(self):
        self.sw = ToggleSwitch(theme.DARK)

    def _click(self, x, y):
        QTest.mouseClick(self.sw, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))

    def test_whole_surface_is_clickable(self):
        w, h = self.sw.width(), self.sw.height()
        dead = [(x, y)
                for y in range(0, h, 3)
                for x in range(0, w, 3)
                if not self.sw.hitButton(QPoint(x, y))]
        self.assertEqual(dead, [], f"{len(dead)} dead points inside the switch")

    def test_click_on_the_right_edge_toggles(self):
        # The far side from the indicator — the case that used to be ignored.
        self.assertFalse(self.sw.isChecked())
        self._click(self.sw.width() - 3, self.sw.height() // 2)
        self.assertTrue(self.sw.isChecked())

    def test_click_in_the_centre_toggles(self):
        self._click(self.sw.width() // 2, self.sw.height() // 2)
        self.assertTrue(self.sw.isChecked())

    def test_click_emits_toggled_once(self):
        seen = []
        self.sw.toggled.connect(seen.append)
        self._click(self.sw.width() - 4, 4)
        self.assertEqual(seen, [True])

    def test_clicks_alternate_state(self):
        for expected in (True, False, True):
            self._click(self.sw.width() // 2, self.sw.height() // 2)
            self.assertIs(self.sw.isChecked(), expected)

    def test_click_outside_is_ignored(self):
        self.assertFalse(self.sw.hitButton(QPoint(-1, 5)))
        self.assertFalse(self.sw.hitButton(QPoint(5, self.sw.height() + 4)))


class NavRailTestCase(unittest.TestCase):
    def test_set_tooltips_pairs_with_items(self):
        rail = NavRail(theme.DARK, [("history", "א"), ("dictionary", "ב")])
        rail.set_tooltips(["Ctrl+1", "Ctrl+2"])
        self.assertEqual([b.toolTip() for b in rail._btns], ["Ctrl+1", "Ctrl+2"])

    def test_set_tooltips_tolerates_short_list(self):
        rail = NavRail(theme.DARK, [("history", "א"), ("dictionary", "ב")])
        rail.set_tooltips(["Ctrl+1"])  # must not raise
        self.assertEqual(rail._btns[0].toolTip(), "Ctrl+1")


if __name__ == "__main__":
    unittest.main()
