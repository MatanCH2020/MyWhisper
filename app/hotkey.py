"""Global hotkeys via the native Win32 RegisterHotKey API.

Much more reliable than a low-level keyboard hook (the `keyboard` library):
RegisterHotKey is delivered by Windows itself as a WM_HOTKEY message, works
without administrator rights, is not blocked by the security software that
silently kills global hooks on some machines, and fails *loudly* if the combo
is already claimed by another app (so the UI can tell the user to pick another).

WM_HOTKEY messages are caught on the Qt main thread through a single
QAbstractNativeEventFilter, so the toggle callback runs on the GUI thread.
"""
import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtWidgets import QApplication, QWidget

log = logging.getLogger("hotkey")

_user32 = ctypes.windll.user32

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
         "shift": MOD_SHIFT, "win": MOD_WIN, "windows": MOD_WIN,
         "super": MOD_WIN, "meta": MOD_WIN}
_KEYS = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
         "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24,
         "end": 0x23, "page up": 0x21, "page down": 0x22, "up": 0x26,
         "down": 0x28, "left": 0x25, "right": 0x27, "esc": 0x1B, "escape": 0x1B}
for _i in range(1, 25):
    _KEYS[f"f{_i}"] = 0x70 + _i - 1


def _parse(hotkey: str):
    """Turn 'ctrl+alt+space' into (modifier_flags, virtual_key_code)."""
    mods, vk = 0, None
    for raw in (hotkey or "").split("+"):
        part = raw.strip().lower()
        if not part:
            continue
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _KEYS:
            vk = _KEYS[part]
        elif len(part) == 1 and (part.isalpha() or part.isdigit()):
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key: {part!r}")
    if vk is None:
        raise ValueError(f"hotkey has no main key: {hotkey!r}")
    return mods, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Routes WM_HOTKEY messages to the callback registered for each id."""

    def __init__(self):
        super().__init__()
        self._cbs = {}
        self._last = None  # identity of the last WM_HOTKEY, for de-duplication

    def add(self, hid, cb):
        self._cbs[hid] = cb

    def remove(self, hid):
        self._cbs.pop(hid, None)

    def nativeEventFilter(self, ev_type, message):
        try:
            et = bytes(ev_type)
        except Exception:
            et = ev_type
        if et == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                # Qt delivers each message to native filters twice (dispatcher +
                # window proc). A real second press has a different .time, so
                # skip only an identical back-to-back delivery.
                identity = (int(msg.wParam), int(msg.time), int(msg.lParam))
                if identity != self._last:
                    self._last = identity
                    cb = self._cbs.get(int(msg.wParam))
                    if cb:
                        cb()
        return False, 0


_filter = None
_host = None   # hidden QWidget that owns the registrations
_hwnd = None
_next_id = 1


def _ensure_host():
    """Create (once) the hidden window that owns the hotkeys and the native
    event filter that routes WM_HOTKEY to callbacks.

    The hotkey MUST be registered to a real window handle: with a NULL hwnd the
    WM_HOTKEY lands on the thread message queue, which Qt does not forward to
    native event filters. A never-shown QWidget gives us such a handle without
    any visible window.
    """
    global _filter, _host, _hwnd
    if _hwnd is None:
        _host = QWidget()
        _host.setWindowFlag(Qt.Tool)
        _hwnd = int(_host.winId())  # realizes the native handle without showing
        _filter = _HotkeyFilter()
        QApplication.instance().installNativeEventFilter(_filter)
    return _hwnd, _filter


def _register(hotkey: str, cb) -> int:
    """Register a global hotkey and return its id. Raises ValueError for a bad
    combo, OSError if Windows refuses it (already registered by another app)."""
    global _next_id
    mods, vk = _parse(hotkey)  # ValueError on bad combo
    hwnd, filt = _ensure_host()
    hid = _next_id
    if not _user32.RegisterHotKey(hwnd, hid, mods | MOD_NOREPEAT, vk):
        raise OSError(f"RegisterHotKey failed for {hotkey!r} (already in use?)")
    _next_id += 1
    filt.add(hid, cb)
    return hid


def _unregister(hid):
    if hid is not None and _hwnd is not None:
        _user32.UnregisterHotKey(_hwnd, hid)
        if _filter is not None:
            _filter.remove(hid)


class HotkeyManager:
    """The main toggle hotkey. start()/rebind()/stop() run on the GUI thread."""

    def __init__(self, hotkey: str, on_toggle):
        self.hotkey = hotkey
        self.on_toggle = on_toggle
        self._id = None

    def start(self):
        self._id = _register(self.hotkey, self.on_toggle)
        log.info("Registered global hotkey '%s'.", self.hotkey)

    def rebind(self, new_hotkey: str):
        """Switch to a new combo. Registers the new one first, so if it is
        invalid or already taken the old binding keeps working."""
        new_id = _register(new_hotkey, self.on_toggle)
        _unregister(self._id)
        self._id = new_id
        self.hotkey = new_hotkey
        log.info("Hotkey rebound to '%s'.", new_hotkey)

    def stop(self):
        _unregister(self._id)
        self._id = None


class TempHotkey:
    """A short-lived global hotkey (Esc-to-cancel, alive only while recording).
    Registration failure is non-fatal — the key just won't cancel."""

    def __init__(self, hotkey: str, cb):
        self.hotkey = hotkey
        self.cb = cb
        self._id = None

    def start(self):
        try:
            self._id = _register(self.hotkey, self.cb)
        except (OSError, ValueError):
            self._id = None

    def stop(self):
        _unregister(self._id)
        self._id = None
