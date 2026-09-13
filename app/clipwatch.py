"""Watches the system clipboard and records what the user copies into clips.py.

Runs on the Qt main thread via QClipboard.dataChanged.

Three things it deliberately does NOT record:

1. **Secrets.** Password managers mark their clipboard writes with well-known
   formats (`ExcludeClipboardContentFromMonitorProcessing`, `Clipboard Viewer
   Ignore`, `CanIncludeInClipboardHistory`) precisely so history tools skip
   them. Honouring that is the difference between a clipboard manager and a
   password logger. 1Password, KeePass and Bitwarden all set one of these.
2. **Our own writes.** paste.py puts transcriptions on the clipboard and then
   restores the previous value — capturing that would both duplicate the
   transcription history and fill the store with echoes. main.py calls
   `suppress()` around those writes.
3. **Anything, while paused.** `set_paused(True)` stops recording without
   unhooking, for when the user is about to copy something private.
"""
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import clips

log = logging.getLogger("clipwatch")

# Clipboard formats that mean "do not put this in clipboard history".
# Matched case-insensitively against the mime type list.
_SECRET_FORMATS = (
    "excludeclipboardcontentfrommonitorprocessing",
    "clipboard viewer ignore",
    "canincludeinclipboardhistory",
    "cancloudsyncclipboard",
    "org.nspasteboard.concealedtype",
)


class ClipboardWatcher(QObject):
    """Records clipboard changes into clips.py. Create after QApplication."""

    _stored = Signal(object, bool)

    def __init__(self, on_change=None, persist_async=True):
        super().__init__()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipboard") if persist_async else None
        self._stored.connect(self._after_store)
        self._closed = False
        self._store_lock = threading.Lock()
        self._generation = 0
        self._on_change = on_change or (lambda: None)
        self._paused = False
        self._suppress_until = 0.0
        self._last_text = None
        self._clip = QApplication.clipboard()
        self._clip.dataChanged.connect(self._on_data_changed)
        # Seed with whatever is already on the clipboard so the first real copy
        # isn't mistaken for a repeat of it.
        try:
            self._last_text = self._clip.text() or None
        except Exception:
            self._last_text = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._clip.dataChanged.disconnect(self._on_data_changed)
        except (AttributeError, RuntimeError, TypeError):
            pass
        if self._executor:
            self._executor.shutdown(wait=True)

    def _after_store(self, text, saved):
        if saved:
            self._on_change()
        elif text == self._last_text:
            self._last_text = None

    def _persist(self, operation, text=None):
        generation = self._generation
        def run():
            try:
                with self._store_lock:
                    saved = generation == self._generation and bool(operation())
            except Exception:
                log.exception("Clipboard persistence failed")
                saved = False
            if self._executor:
                self._stored.emit(text, saved)
            else:
                self._after_store(text, saved)
        if self._executor:
            self._executor.submit(run)
        else:
            run()

    def clear_history(self):
        # Discard older queued copies, then clear after any current write finishes.
        # New clipboard events after this user action belong to the next generation.
        with self._store_lock:
            self._generation += 1
            self._last_text = None
            return clips.clear()

    # ---- control ----
    def set_paused(self, paused: bool):
        self._paused = bool(paused)
        log.info("Clipboard capture %s.", "paused" if paused else "resumed")

    def is_paused(self) -> bool:
        return self._paused

    def suppress(self, seconds: float = 3.0):
        """Ignore clipboard changes for a moment — used around our own writes.

        Time-boxed rather than a strict counter: paste.py writes twice (text,
        then the restored previous value) with a configurable delay between, and
        a missed signal would otherwise leave capture off indefinitely.
        """
        self._suppress_until = max(self._suppress_until, time.monotonic() + seconds)

    # ---- capture ----
    def _on_data_changed(self):
        if self._closed or self._paused or time.monotonic() < self._suppress_until:
            return
        try:
            md = self._clip.mimeData()
        except Exception:
            log.exception("Could not read the clipboard")
            return
        if md is None:
            return
        if self._is_secret(md):
            log.info("Clipboard change skipped: marked as sensitive by its source.")
            return
        try:
            if md.hasText() and (md.text() or "").strip():
                text = md.text()
                if text == self._last_text:
                    return  # same value re-announced; not a new copy
                self._last_text = text
                self._persist(lambda: clips.add_text(text), text)
            elif md.hasImage():
                # Detach on the GUI thread; workers never access QClipboard.
                img = self._clip.image()
                if img is not None and not img.isNull():
                    snapshot = img.copy()
                    self._persist(lambda: clips.add_image(lambda p: snapshot.save(str(p), "PNG")))
        except Exception:
            log.exception("Failed to record a clipboard change")

    @staticmethod
    def _is_secret(md) -> bool:
        try:
            formats = [f.lower() for f in md.formats()]
        except Exception:
            return True  # unknown sensitivity: do not capture
        return any(any(marker in f for f in formats) for marker in _SECRET_FORMATS)

    def _save_image(self, path) -> bool:
        img = self._clip.image()
        if img is None or img.isNull():
            return False
        return bool(img.save(str(path), "PNG"))
