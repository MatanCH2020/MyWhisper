"""Frozen-bundle smoke check: no recording, clipboard reads or model download."""
import importlib
from pathlib import Path
import sys


def verify():
    if sys.platform != "darwin":
        raise RuntimeError("Mac bundle verification requires macOS")
    for name in ("AppKit", "ApplicationServices", "AVFoundation", "Quartz", "CoreFoundation",
                 "sounddevice", "av", "wordfreq", "mlx.core", "mlx_whisper.transcribe",
                 "macos_hotkey", "macos_system", "macos_clipboard", "macos_setup", "ui"):
        importlib.import_module(name)
    import numpy as np
    from local_vad import LocalVAD
    if len(LocalVAD().filter(np.zeros(16000, dtype=np.float32))):
        raise RuntimeError("Silence incorrectly passed VAD")
    root = Path(__file__).resolve().parent
    if not (root / "CHANGELOG.md").is_file():
        raise RuntimeError("Changelog is missing from bundle")
    if any(name in sys.modules for name in ("chatgpt_auth", "faster_whisper", "winclipboard")):
        raise RuntimeError("Wrong platform backend was imported")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    from PySide6.QtCore import Qt
    from ui import AppUI, MainWindow
    import theme
    from config import DEFAULTS
    import os
    qapp = QApplication.instance() or QApplication([])
    demo = [{"id": "demo", "text": "בדיקת עברית ב־Mac עם GitHub ו־Python.", "time": "2026-10-25 12:00"}]
    ui = AppUI(dict(DEFAULTS), lambda: 0, lambda cfg: True, lambda: demo,
               lambda: True, lambda cue: None, lambda *args: True)
    window = MainWindow(ui, theme.DARK)
    output = Path(os.environ["MYWHISPER_QA_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    for palette in (theme.DARK, theme.LIGHT):
        window.set_palette(palette)
        window.show()
        qapp.processEvents()
        image = QImage(window.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        window.render(image)
        if not image.save(str(output / ("macos-preview-" + palette["name"] + ".png"))):
            raise RuntimeError("Could not save UI preview")
    window.hide()
    print("Mac bundle imports, local assets and VAD silence check passed; hardware behavior is not verified")
