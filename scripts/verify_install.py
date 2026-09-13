"""Import/UI smoke check without microphone access, hotkeys or model downloads."""
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtWidgets import QApplication

qapp = QApplication.instance() or QApplication([])
import main
import theme
from ui import ChangelogDialog

assert ChangelogDialog(None, theme.LIGHT)._parse_versions()
print("Installation imports and Qt smoke check passed")
