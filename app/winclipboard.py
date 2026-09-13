"""Windows clipboard transactions. No Qt objects are accessed by workers.

Sequence check and restore share OpenClipboard's OS lock, so another application's
copy can never be overwritten between the check and restore. Supported snapshots:
text, DIB images, file lists, HTML/RTF/PNG and password-manager exclusion markers.
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getclipboardsequencenumber
"""
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
import time


def _bind(dll, name, result, args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


u, k = c.WinDLL("user32", use_last_error=True), c.WinDLL("kernel32", use_last_error=True)
_create = _bind(u, "CreateWindowExW", w.HWND,
                [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int,
                 c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID])
_destroy = _bind(u, "DestroyWindow", w.BOOL, [w.HWND])
_open = _bind(u, "OpenClipboard", w.BOOL, [w.HWND])
_close = _bind(u, "CloseClipboard", w.BOOL, [])
_empty = _bind(u, "EmptyClipboard", w.BOOL, [])
_enum = _bind(u, "EnumClipboardFormats", w.UINT, [w.UINT])
_get = _bind(u, "GetClipboardData", w.HANDLE, [w.UINT])
_set = _bind(u, "SetClipboardData", w.HANDLE, [w.UINT, w.HANDLE])
_sequence = _bind(u, "GetClipboardSequenceNumber", w.DWORD, [])
_name = _bind(u, "GetClipboardFormatNameW", c.c_int, [w.UINT, w.LPWSTR, c.c_int])
_alloc = _bind(k, "GlobalAlloc", w.HGLOBAL, [w.UINT, c.c_size_t])
_free = _bind(k, "GlobalFree", w.HGLOBAL, [w.HGLOBAL])
_size = _bind(k, "GlobalSize", c.c_size_t, [w.HGLOBAL])
_lock = _bind(k, "GlobalLock", w.LPVOID, [w.HGLOBAL])
_unlock = _bind(k, "GlobalUnlock", w.BOOL, [w.HGLOBAL])
_STANDARD = {1, 7, 8, 13, 15, 16, 17}
_REGISTERED = {"html format", "rich text format", "png",
               "excludeclipboardcontentfrommonitorprocessing", "clipboard viewer ignore",
               "canincludeinclipboardhistory", "cancloudsyncclipboard",
               "org.nspasteboard.concealedtype"}


class WindowsClipboard:
    def __enter__(self):
        self.hwnd = _create(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        if not self.hwnd:
            raise c.WinError(c.get_last_error())
        return self

    def __exit__(self, *args):
        _destroy(self.hwnd)

    @contextmanager
    def opened(self):
        for _ in range(50):
            if _open(self.hwnd):
                break
            time.sleep(0.01)
        else:
            raise OSError("Clipboard is busy")
        try:
            yield
        finally:
            _close()

    def _snapshot(self):
        result, fmt, total = [], 0, 0
        while True:
            c.set_last_error(0)
            fmt = _enum(fmt)
            if not fmt:
                if c.get_last_error():
                    raise c.WinError(c.get_last_error())
                return result
            name = c.create_unicode_buffer(256)
            if fmt not in _STANDARD:
                _name(fmt, name, len(name))
                if name.value.lower() not in _REGISTERED:
                    continue
            handle = _get(fmt)
            if not handle:
                raise OSError("Could not snapshot a supported clipboard format")
            size = _size(handle)
            total += size
            if total > 64 * 1024 * 1024:
                raise OSError("Clipboard snapshot exceeds 64 MB")
            ptr = _lock(handle)
            if not ptr:
                raise OSError("Could not lock clipboard data")
            try:
                result.append((fmt, c.string_at(ptr, size)))
            finally:
                _unlock(handle)

    def _replace(self, formats):
        handles = []
        try:
            # Allocate before clearing, so allocation failures keep the old copy.
            for fmt, data in formats:
                h = _alloc(2, max(1, len(data)))
                if not h:
                    raise MemoryError("Clipboard allocation failed")
                handles.append([fmt, h])
                ptr = _lock(h)
                if not ptr:
                    raise OSError("Clipboard allocation lock failed")
                try:
                    c.memmove(ptr, data, len(data))
                finally:
                    _unlock(h)
            if not _empty():
                raise OSError("Could not empty clipboard")
            for item in handles:
                if not _set(*item):
                    raise OSError("Could not set clipboard format")
                item[1] = None  # ownership transferred to Windows
        finally:
            for _, h in handles:
                if h:
                    _free(h)

    def write_text(self, text, preserve=True):
        with self.opened():
            before = self._snapshot() if preserve else None
            try:
                self._replace([(13, (text + "\0").encode("utf-16-le"))])
            except Exception:
                if before is not None:
                    self._replace(before)
                raise
            return before, _sequence()

    def restore(self, before, sequence):
        with self.opened():
            if not sequence or _sequence() != sequence:
                return False
            self._replace(before)
            return True
