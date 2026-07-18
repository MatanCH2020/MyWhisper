"""Deliver transcribed text by pasting it into the active field (clipboard + Ctrl+V)."""
import time
import keyboard
import pyperclip

# Modifiers to release before injecting Ctrl+V: with a hotkey like ctrl+space
# the user may still be physically holding Ctrl/Alt, which some apps combine
# with the injected keystroke (turning it into Ctrl+Alt+V etc.).
_MODIFIERS = ("ctrl", "alt", "shift", "windows")


def paste_text(text: str, restore_clipboard: bool = True, restore_delay: float = 0.5):
    """Copy text to the clipboard and inject Ctrl+V into the focused window.

    If restore_clipboard is True, the user's previous clipboard content is
    restored after restore_delay seconds (enough for the target app to consume
    the paste; raise it in config for slow apps like heavy Electron windows).
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
    # Small delay so the clipboard write settles before paste.
    time.sleep(0.05)
    for mod in _MODIFIERS:
        try:
            keyboard.release(mod)
        except Exception:
            pass
    keyboard.send("ctrl+v")

    if restore_clipboard and previous is not None:
        # Wait for the target app to consume the paste before restoring.
        time.sleep(max(0.1, float(restore_delay)))
        try:
            pyperclip.copy(previous)
        except Exception:
            pass
