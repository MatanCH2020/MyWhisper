"""Platform capabilities and writable locations; never import native backends here."""
import sys
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _window_api():
    """Declare HWND signatures explicitly: ctypes defaults truncate x64 handles."""
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("user32", use_last_error=True)
    for name, args, result in (
        ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
        ("GetForegroundWindow", [], wintypes.HWND),
        ("IsIconic", [wintypes.HWND], wintypes.BOOL),
        ("GetLastInputInfo", [ctypes.c_void_p], wintypes.BOOL),
    ):
        fn = getattr(api, name)
        fn.argtypes, fn.restype = args, result
    return api


def foreground_context():
    """Foreground handle and last input tick, for cancelling deferred activation."""
    from PySide6.QtWidgets import QApplication
    if sys.platform != "win32" or QApplication.platformName() == "offscreen":
        active = QApplication.activeWindow()
        return (int(active.winId()) if active else 0, 0)
    import ctypes
    from ctypes import wintypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    api = _window_api()
    ok = api.GetLastInputInfo(ctypes.byref(info))
    return (int(api.GetForegroundWindow() or 0), info.dwTime if ok else None)


def is_foreground(window):
    return (window.isVisible() and not window.isMinimized()
            and foreground_context()[0] == int(window.winId()))


def bring_forward(window):
    """Activate on an explicit user request; return verified foreground state."""
    from PySide6.QtWidgets import QApplication
    if QApplication.platformName() == "offscreen":
        window.raise_()
        window.activateWindow()
        return window.isActiveWindow()
    if IS_MAC:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()
    else:
        api = _window_api()
        hwnd = int(window.winId())
        # SW_RESTORE restores the previous normal/maximized placement.
        if api.IsIconic(hwnd):
            api.ShowWindow(hwnd, 9)
        elif not window.isVisible():
            window.show()
        # Qt owns maximized geometry for frameless windows. A second native
        # SW_MAXIMIZE overwrites its restore placement with the screen-sized rect.
        window.raise_()
        window.activateWindow()
        api.SetForegroundWindow(hwnd)
    return is_foreground(window)
