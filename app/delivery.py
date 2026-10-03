"""Guard the dictation destination without stealing foreground focus."""
import ctypes
import threading


def foreground_window():
    try:
        user = ctypes.WinDLL("user32")
        user.GetForegroundWindow.restype = ctypes.c_void_p
        return user.GetForegroundWindow() or 0
    except (AttributeError, OSError):
        return 0


def same_destination(destination):
    # An unknown/lost destination must never receive synthetic Ctrl+V.
    return bool(destination) and foreground_window() == destination


class DestinationGuard:
    """Observe foreground changes on the Qt/Windows message-loop thread.

    A switch away and back still counts as a change. Keep the callback alive
    until the GUI thread unhooks it after worker completion.
    """
    def __init__(self):
        self.window = foreground_window()
        self.changed = threading.Event()
        self._hook = None
        self._callback = None

    def start(self):
        try:
            user = ctypes.WinDLL("user32")
            callback_type = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ulong,
                                               ctypes.c_void_p, ctypes.c_long, ctypes.c_long,
                                               ctypes.c_ulong, ctypes.c_ulong)
            self._callback = callback_type(
                lambda hook, event, hwnd, obj, child, thread, timestamp:
                self.changed.set() if hwnd != self.window else None)
            user.SetWinEventHook.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                            callback_type, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
            user.SetWinEventHook.restype = ctypes.c_void_p
            self._hook = user.SetWinEventHook(3, 3, None, self._callback, 0, 0, 0)
        except (AttributeError, OSError):
            pass
        return self

    def unchanged(self):
        return not self.changed.is_set() and same_destination(self.window)

    def close(self):
        if self._hook:
            user = ctypes.WinDLL("user32")
            user.UnhookWinEvent.argtypes = [ctypes.c_void_p]
            user.UnhookWinEvent(self._hook)
            self._hook = None
        self._callback = None
