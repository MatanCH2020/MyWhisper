"""Deliver transcribed text by pasting it into the active field (clipboard + Ctrl+V).

The Ctrl+V keystroke is injected with the native Win32 keybd_event API rather
than the `keyboard` library, so pasting works even on machines where that
library's input hooks are blocked by security software.
"""
import ctypes
import time
import pyperclip

_user32 = ctypes.windll.user32

_VK_CONTROL, _VK_MENU, _VK_SHIFT = 0x11, 0x12, 0x10
_VK_LWIN, _VK_RWIN, _VK_V = 0x5B, 0x5C, 0x56
_KEYUP = 0x0002
# Modifiers to lift before injecting Ctrl+V: with a hotkey like ctrl+space the
# user may still be holding keys that would otherwise combine with the paste.
_STRAY_MODS = (_VK_CONTROL, _VK_MENU, _VK_SHIFT, _VK_LWIN, _VK_RWIN)


def _key(vk, up=False):
    _user32.keybd_event(vk, 0, _KEYUP if up else 0, 0)


def paste_text(text: str, restore_clipboard: bool = True, restore_delay: float = 0.5):
    """Copy text to the clipboard and inject Ctrl+V into the focused window.

    If restore_clipboard is True, the user's previous clipboard content is
    restored after restore_delay seconds (raise it in config for apps that are
    slow to consume the paste).
    """
    if not text:
        return

    previous = None
    if restore_clipboard:
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None

    pyperclip.copy(text)
    time.sleep(0.05)  # let the clipboard write settle before pasting

    for vk in _STRAY_MODS:
        _key(vk, up=True)
    _key(_VK_CONTROL)            # Ctrl down
    _key(_VK_V)                  # V down
    _key(_VK_V, up=True)         # V up
    _key(_VK_CONTROL, up=True)   # Ctrl up

    if restore_clipboard and previous is not None:
        time.sleep(max(0.1, float(restore_delay)))
        try:
            pyperclip.copy(previous)
        except Exception:
            pass
