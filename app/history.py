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

log = logging.getLogger("history")

HISTORY_PATH = Path(__file__).resolve().parent.parent / "history.json"
MAX_ENTRIES = 300

_lock = threading.Lock()


def _read():
    if not HISTORY_PATH.exists():
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return entries if isinstance(entries, list) else []


def _write(entries):
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.error("Failed to write: %s", e)


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


def add(text: str):
    """Prepend a transcription with a local timestamp; cap the list length."""
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        entries = _read()
        entries.insert(0, {"id": _new_id(),
                           "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                           "text": text})
        del entries[MAX_ENTRIES:]
        _write(entries)


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
                _write(entries)
                return


def delete(entry_id: str):
    """Remove the entry with the given stable id."""
    with _lock:
        entries = _read()
        kept = [e for e in entries if e.get("id") != entry_id]
        if len(kept) != len(entries):
            _write(kept)


def clear():
    with _lock:
        try:
            if HISTORY_PATH.exists():
                HISTORY_PATH.unlink()
        except OSError:
            pass
