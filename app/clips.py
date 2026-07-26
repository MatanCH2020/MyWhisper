"""Clipboard history store — every text/image the user copies, kept on disk.

Independent of the transcription history in `history.py`: that one records what
MyWhisper produced, this one records what the user copied from anywhere.

Text is deliberately **uncapped in count** — thousands of entries cost a couple
of MB and the whole point is a long, searchable history. What *is* capped:

* the size of a single entry (`MAX_TEXT_CHARS`) — one copied log file would
  otherwise bloat the store and slow every load;
* the number of images (`MAX_IMAGES`), which are megabytes each, not bytes.

Re-copying something already in the store moves it back to the top instead of
adding a duplicate, so the list stays useful rather than repetitive.
"""
import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("clips")

_ROOT = Path(__file__).resolve().parent.parent
CLIPS_PATH = _ROOT / "clips.json"
IMAGE_DIR = _ROOT / "clip_images"

# A single entry larger than this is skipped (characters, not bytes).
MAX_TEXT_CHARS = 100_000
# Images are heavy, so unlike text they are capped by count.
MAX_IMAGES = 30
# Safety valve only — far above any realistic history. Keeps a runaway writer
# (a script spamming the clipboard) from growing the file without bound.
MAX_ENTRIES = 50_000

_lock = threading.Lock()


def _read():
    if not CLIPS_PATH.exists():
        return []
    try:
        with open(CLIPS_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return entries if isinstance(entries, list) else []


def _write(entries):
    try:
        with open(CLIPS_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.error("Failed to write clips: %s", e)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def load():
    """All clips, newest first. Each: {'id','time','kind','text'|'path'}."""
    with _lock:
        return _read()


def add_text(text: str):
    """Store a copied string. Returns the entry, or None if it was skipped.

    Skips blanks and anything over MAX_TEXT_CHARS. An exact repeat is moved to
    the top (with a refreshed timestamp) rather than duplicated.
    """
    if not text or not text.strip():
        return None
    if len(text) > MAX_TEXT_CHARS:
        log.info("Clip skipped: %d chars exceeds the %d cap.",
                 len(text), MAX_TEXT_CHARS)
        return None
    with _lock:
        entries = _read()
        for i, e in enumerate(entries):
            if e.get("kind") == "text" and e.get("text") == text:
                entry = entries.pop(i)
                entry["time"] = _now()
                entries.insert(0, entry)
                _write(entries)
                return entry
        entry = {"id": _new_id(), "time": _now(), "kind": "text", "text": text}
        entries.insert(0, entry)
        del entries[MAX_ENTRIES:]
        _write(entries)
        return entry


def add_image(save_png) -> dict:
    """Store a copied image.

    *save_png* is called with a destination Path and must return True on
    success — keeps this module free of any Qt dependency. Oldest images beyond
    MAX_IMAGES are deleted, files included.
    """
    try:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error("Cannot create the image folder: %s", e)
        return None
    cid = _new_id()
    path = IMAGE_DIR / f"{cid}.png"
    try:
        if not save_png(path):
            return None
    except Exception:
        log.exception("Failed to save a clipboard image")
        return None
    with _lock:
        entries = _read()
        entry = {"id": cid, "time": _now(), "kind": "image", "path": str(path)}
        entries.insert(0, entry)
        # Trim surplus images (and their files); text entries are untouched.
        seen = 0
        keep = []
        for e in entries:
            if e.get("kind") == "image":
                seen += 1
                if seen > MAX_IMAGES:
                    _remove_file(e.get("path"))
                    continue
            keep.append(e)
        del keep[MAX_ENTRIES:]
        _write(keep)
        return entry


def _remove_file(path):
    if not path:
        return
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except OSError:
        pass


def delete(clip_id: str):
    """Remove one clip. Returns (entry, index) for undo, or None if absent.

    An image's file is left on disk so the entry can be restored; clear() and
    the image cap are what actually delete files.
    """
    with _lock:
        entries = _read()
        for i, e in enumerate(entries):
            if e.get("id") == clip_id:
                removed = entries.pop(i)
                _write(entries)
                return removed, i
        return None


def restore(entry: dict, index: int):
    """Re-insert a deleted clip at its old position (undo of delete())."""
    if not isinstance(entry, dict) or not entry.get("id"):
        return
    with _lock:
        entries = _read()
        if any(e.get("id") == entry["id"] for e in entries):
            return
        entries.insert(max(0, min(index, len(entries))), entry)
        _write(entries)


def clear():
    """Drop every clip and delete the stored image files."""
    with _lock:
        for e in _read():
            if e.get("kind") == "image":
                _remove_file(e.get("path"))
        try:
            if CLIPS_PATH.exists():
                CLIPS_PATH.unlink()
        except OSError:
            pass


def search(query: str, entries=None):
    """Clips whose text contains *query* (case-insensitive).

    Images have no text to match, so a non-empty query filters them out.
    """
    items = _read() if entries is None else entries
    q = (query or "").strip().lower()
    if not q:
        return list(items)
    return [e for e in items
            if e.get("kind") == "text" and q in (e.get("text") or "").lower()]


def preview(entry, limit=90) -> str:
    """One-line label for the picker list."""
    if not isinstance(entry, dict):
        return ""
    if entry.get("kind") == "image":
        return "🖼  תמונה"
    text = " ".join((entry.get("text") or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
