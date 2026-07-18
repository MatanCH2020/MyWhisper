"""Global hotkey registration (toggle mode) using the keyboard library."""
import logging
import keyboard

log = logging.getLogger("hotkey")


class HotkeyManager:
    """Registers a single global hotkey that toggles a callback on each press."""

    def __init__(self, hotkey: str, on_toggle):
        self.hotkey = hotkey
        self.on_toggle = on_toggle
        self._handle = None

    def start(self):
        # suppress=False so the keypress still reaches other apps if needed.
        self._handle = keyboard.add_hotkey(self.hotkey, self.on_toggle)
        log.info("Listening for '%s' (press to start/stop recording).", self.hotkey)

    def stop(self):
        if self._handle is not None:
            keyboard.remove_hotkey(self._handle)
            self._handle = None
