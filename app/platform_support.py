"""Platform capabilities and writable locations; never import native backends here."""
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
CLOUD_SUPPORTED = not IS_MAC
ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ID = "com.matandigital.mywhisper"


def data_dir():
    path = Path.home() / "Library/Application Support/MatanDigital/MyWhisper" if IS_MAC else ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir():
    path = Path.home() / "Library/Logs/MatanDigital/MyWhisper" if IS_MAC else ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


def defaults():
    return ({"backend": "mlx", "model": "mlx-community/ivrit-ai-whisper-large-v3-mlx",
             "device": "metal", "hotkey": "ctrl+alt+space", "clipboard_hotkey": "ctrl+alt+v"}
            if IS_MAC else {})


def bring_forward(window):
    if IS_MAC:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        window.raise_()
        window.activateWindow()
    else:
        import ctypes
        hwnd = int(window.winId())
        ctypes.windll.user32.ShowWindow(hwnd, 3 if window.isMaximized() else 5)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
