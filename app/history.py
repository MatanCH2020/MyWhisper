"""Persist transcription history to history.json in the project root."""
import json
from datetime import datetime
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "history.json"
MAX_ENTRIES = 300


def load():
    """Return the list of entries, newest first. Each: {'time': str, 'text': str}."""
    if not HISTORY_PATH.exists():
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def add(text: str):
    """Prepend a transcription with a local timestamp; cap the list length."""
    text = (text or "").strip()
    if not text:
        return
    entries = load()
    entries.insert(0, {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text})
    del entries[MAX_ENTRIES:]
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[history] Failed to write: {e}")


def update(index: int, new_text: str):
    """Replace the text of the entry at the given position (newest = 0)."""
    new_text = (new_text or "").strip()
    if not new_text:
        return
    entries = load()
    if 0 <= index < len(entries):
        entries[index]["text"] = new_text
        try:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[history] Failed to write: {e}")


def delete(index: int):
    """Remove the entry at the given position (newest = 0)."""
    entries = load()
    if 0 <= index < len(entries):
        del entries[index]
        try:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[history] Failed to write: {e}")


def clear():
    try:
        if HISTORY_PATH.exists():
            HISTORY_PATH.unlink()
    except OSError:
        pass
