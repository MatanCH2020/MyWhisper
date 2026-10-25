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
    print("Mac bundle imports, local assets and VAD silence check passed; hardware behavior is not verified")
