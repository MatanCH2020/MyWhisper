"""Persist transcription history to history.json in the project root.

Entries carry a stable random id (not their list position), so UI edits and
deletes can never hit the wrong entry when a new transcription lands while a
dialog is open. Every read-modify-write cycle holds a module lock — add() runs
on the transcription worker thread while update()/delete() run on the Qt
thread, and unlocked cycles could silently drop entries.
"""
import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from safe_json import atomic_save, read_json

log = logging.getLogger("history")

HISTORY_PATH = Path(__file__).resolve().parent.parent / "history.json"
MAX_ENTRIES = 300

_lock = threading.Lock()


def _read():
    return read_json(HISTORY_PATH, [], lambda items: isinstance(items, list) and all(
        isinstance(e, dict) and isinstance(e.get("text"), str)
        and isinstance(e.get("time", ""), str)
        and ("id" not in e or isinstance(e["id"], str)) for e in items))


def _write(entries):
    return atomic_save(HISTORY_PATH, entries)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def load():
    """Return entries, newest first. Each: {'id': str, 'time': str, 'text': str}.

    Entries written by older versions (no id) get one assigned and persisted.
    """
    with _lock:
        entries = _read()
        migrated = False
        for e in entries:
            if isinstance(e, dict) and "id" not in e:
                e["id"] = _new_id()
                migrated = True
        if migrated:
            _write(entries)
        return entries


def add(text: str, *, original_text=None, edit_status=None, edit_ms=None, edit_model=None):
    """Prepend a transcription with a local timestamp; cap the list length."""
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        entries = _read()
        entry = {"id": _new_id(), "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "text": text}
        if isinstance(original_text, str):
            entry["original_text"] = original_text
        if isinstance(edit_status, str):
            entry["edit_status"] = edit_status
        if isinstance(edit_ms, int):
            entry["edit_ms"] = edit_ms
        if isinstance(edit_model, str) and edit_model:
            entry["edit_model"] = edit_model
        entries.insert(0, entry)
        del entries[MAX_ENTRIES:]
        return _write(entries)


def update(entry_id: str, new_text: str):
    """Replace the text of the entry with the given stable id."""
    new_text = (new_text or "").strip()
    if not new_text:
        return
    with _lock:
        entries = _read()
        for e in entries:
            if e.get("id") == entry_id:
                e["text"] = new_text
                return _write(entries)
        return False


def delete(entry_id: str):
    """Remove the entry with the given stable id.

    Returns (entry, index) so the caller can offer an undo, or None if the id
    was not present.
    """
    with _lock:
        entries = _read()
        for i, e in enumerate(entries):
            if e.get("id") == entry_id:
                removed = entries.pop(i)
                return (removed, i) if _write(entries) else None
        return None


def restore(entry: dict, index: int):
    """Re-insert a deleted entry at its original position (undo of delete()).

    A no-op if an entry with the same id is already back, so a double undo
    cannot duplicate it.
    """
    if not isinstance(entry, dict) or not entry.get("id"):
        return
    with _lock:
        entries = _read()
        if any(e.get("id") == entry["id"] for e in entries):
            return
        entries.insert(max(0, min(index, len(entries))), entry)
        del entries[MAX_ENTRIES:]
        return _write(entries)


def clear():
    with _lock:
        _read()  # Preserve corrupt data / detect unreadable files before replacing.
        return _write([])
