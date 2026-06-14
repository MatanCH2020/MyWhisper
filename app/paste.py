"""Deliver transcribed text by pasting it into the active field (clipboard + Ctrl+V)."""
import time
import keyboard
import pyperclip


def paste_text(text: str, restore_clipboard: bool = True):
    """Copy text to the clipboard and inject Ctrl+V into the focused window.

    If restore_clipboard is True, the user's previous clipboard content is
    restored afterwards so we don't clobber what they had copied.
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
    keyboard.send("ctrl+v")

    if restore_clipboard and previous is not None:
        # Wait for the target app to consume the paste before restoring.
        time.sleep(0.3)
        try:
            pyperclip.copy(previous)
        except Exception:
            pass
