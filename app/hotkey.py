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

    def rebind(self, new_hotkey: str):
        """Switch to a new hotkey live. Registers the new binding first, so an
        invalid combo raises before the old one is removed (old stays working)."""
        new_handle = keyboard.add_hotkey(new_hotkey, self.on_toggle)  # raises on bad combo
        if self._handle is not None:
            try:
                keyboard.remove_hotkey(self._handle)
            except (KeyError, ValueError):
                pass
        self._handle = new_handle
        self.hotkey = new_hotkey
        log.info("Hotkey rebound to '%s'.", new_hotkey)

    def stop(self):
        if self._handle is not None:
            keyboard.remove_hotkey(self._handle)
            self._handle = None
