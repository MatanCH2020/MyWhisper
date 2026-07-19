"""Detect whether a fullscreen app (typically a game or video) is in the
foreground, so MyWhisper can release the model and free the GPU/CPU for it."""
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_MONITOR_DEFAULTTONEAREST = 2


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def foreground_is_fullscreen() -> bool:
    """True if the focused window covers its entire monitor and isn't the
    desktop/shell (so the taskbar is hidden — the hallmark of a fullscreen game
    or video). Best-effort: any failure returns False (never over-releases)."""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False
        # Ignore the desktop and shell windows (they are "fullscreen" but idle).
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False

        win = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(win)):
            return False
        hmon = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        # The window spans (at least) the whole monitor.
        return (win.left <= m.left and win.top <= m.top and
                win.right >= m.right and win.bottom >= m.bottom)
    except Exception:
        return False
