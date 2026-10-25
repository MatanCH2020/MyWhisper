"""Carbon hotkey registration (not a global keystroke logger). GUI-thread owned."""
import ctypes as c
import logging
from PySide6.QtCore import QObject, Signal, Qt

log = logging.getLogger(__name__)


def fourcc(s):
    return int.from_bytes(s.encode("ascii"), "big")


class HotkeyID(c.Structure):
    _fields_ = [("signature", c.c_uint32), ("id", c.c_uint32)]


class EventType(c.Structure):
    _fields_ = [("eventClass", c.c_uint32), ("eventKind", c.c_uint32)]


# Physical ANSI key positions; independent of the currently selected Hebrew layout.
_KEYS = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6,
         "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14,
         "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
         "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
         "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
         "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41,
         "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
         "tab": 48, "space": 49, "`": 50, "backspace": 51, "esc": 53,
         "home": 115, "page up": 116, "delete": 117, "end": 119,
         "page down": 121, "left": 123, "right": 124, "down": 125, "up": 126}
_KEYS.update({f"f{i}": key for i, key in enumerate(
    [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113,
     106, 64, 79, 80, 90], 1)})
_MODS = {"cmd": 256, "command": 256, "meta": 256, "windows": 256,
         "shift": 512, "alt": 2048, "option": 2048, "ctrl": 4096, "control": 4096}


def _parse(combo):
    mods, key = 0, None
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _KEYS and key is None:
            key = _KEYS[part]
        else:
            raise ValueError("Unsupported hotkey")
    if key is None:
        raise ValueError("Missing key")
    return mods, key


class _Registry(QObject):
    fired = Signal(int)

    def __init__(self):
        super().__init__()
        self.callbacks, self.refs, self.held = {}, {}, set()
        self.next_id = 1
        self.lib = c.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self.callback_type = c.CFUNCTYPE(c.c_int32, c.c_void_p, c.c_void_p, c.c_void_p)
        signatures = {
            "GetApplicationEventTarget": ([], c.c_void_p),
            "InstallEventHandler": ([c.c_void_p, self.callback_type, c.c_uint32,
                                     c.POINTER(EventType), c.c_void_p, c.POINTER(c.c_void_p)], c.c_int32),
            "GetEventKind": ([c.c_void_p], c.c_uint32),
            "GetEventParameter": ([c.c_void_p, c.c_uint32, c.c_uint32, c.c_void_p,
                                   c.c_uint32, c.c_void_p, c.c_void_p], c.c_int32),
            "RegisterEventHotKey": ([c.c_uint32, c.c_uint32, HotkeyID, c.c_void_p,
                                     c.c_uint32, c.POINTER(c.c_void_p)], c.c_int32),
            "UnregisterEventHotKey": ([c.c_void_p], c.c_int32),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.target = self.lib.GetApplicationEventTarget()
        self.callback = self.callback_type(self._event)
        types = (EventType * 2)(EventType(fourcc("keyb"), 6), EventType(fourcc("keyb"), 9))
        self.handler = c.c_void_p()
        if self.lib.InstallEventHandler(self.target, self.callback, 2, types, None, c.byref(self.handler)):
            raise OSError("Cannot install hotkey handler")
        self.fired.connect(self._dispatch, Qt.QueuedConnection)

    def _event(self, _next, event, _data):
        identity = HotkeyID()
        if self.lib.GetEventParameter(event, fourcc("----"), fourcc("hkid"), None,
                                      c.sizeof(identity), None, c.byref(identity)):
            return -9874
        if identity.signature != fourcc("MWsp"):
            return -9874
        if self.lib.GetEventKind(event) == 9:
            self.held.discard(identity.id)
        elif identity.id not in self.held:
            self.held.add(identity.id)
            self.fired.emit(identity.id)
        return 0

    def _dispatch(self, hid):
        callback = self.callbacks.get(hid)
        if callback:
            try:
                callback()
            except Exception:
                log.exception("Hotkey callback failed")

    def register(self, combo, callback):
        mods, key = _parse(combo)
        hid = self.next_id
        self.next_id += 1
        ref = c.c_void_p()
        status = self.lib.RegisterEventHotKey(key, mods, HotkeyID(fourcc("MWsp"), hid),
                                             self.target, 0, c.byref(ref))
        if status:
            raise OSError(f"Shortcut unavailable ({status})")
        self.refs[hid], self.callbacks[hid] = ref, callback
        return hid

    def unregister(self, hid):
        self.callbacks.pop(hid, None)
        self.held.discard(hid)
        ref = self.refs.pop(hid, None)
        if ref:
            self.lib.UnregisterEventHotKey(ref)


_registry = None


def _register(combo, callback):
    global _registry
    if _registry is None:
        _registry = _Registry()
    return _registry.register(combo, callback)


def _unregister(hid):
    if _registry is not None:
        _registry.unregister(hid)


class HotkeyManager:
    def __init__(self, hotkey, on_toggle):
        self.hotkey, self.on_toggle = hotkey, on_toggle
        self._id, self._paused = None, False

    def start(self):
        if not self._paused and self._id is None:
            self._id = _register(self.hotkey, self.on_toggle)

    def stop(self):
        _unregister(self._id)
        self._id = None

    def rebind(self, combo):
        _parse(combo)
        if combo == self.hotkey:
            return
        if not self._paused:
            new_id = _register(combo, self.on_toggle)
            self.stop()
            self._id = new_id
        self.hotkey = combo

    def pause(self):
        self._paused = True
        self.stop()

    def resume(self):
        self._paused = False
        self.start()


class TempHotkey(HotkeyManager):
    def start(self):
        try:
            super().start()
        except (OSError, ValueError):
            log.warning("Temporary cancel shortcut unavailable")
