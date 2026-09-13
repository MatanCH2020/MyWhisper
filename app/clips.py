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

from safe_json import atomic_save, read_json, report_error

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
    return read_json(CLIPS_PATH, [], lambda items: isinstance(items, list) and all(
        isinstance(e, dict) and isinstance(e.get("id"), str)
        and isinstance(e.get("time", ""), str)
        and e.get("kind") in ("text", "image")
        and isinstance(e.get("text" if e.get("kind") == "text" else "path"), str)
        for e in items))


def _write(entries):
    return atomic_save(CLIPS_PATH, entries)


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
                return entry if _write(entries) else None
        entry = {"id": _new_id(), "time": _now(), "kind": "text", "text": text}
        entries.insert(0, entry)
        del entries[MAX_ENTRIES:]
        return entry if _write(entries) else None


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
            _remove_file(path)
            return None
    except Exception:
        _remove_file(path)
        log.exception("Failed to save a clipboard image")
        return None
    with _lock:
        entries = _read()
        entry = {"id": cid, "time": _now(), "kind": "image", "path": str(path)}
        entries.insert(0, entry)
        # Trim surplus images (and their files); text entries are untouched.
        seen = 0
        keep, evicted = [], []
        for e in entries:
            if e.get("kind") == "image":
                seen += 1
                if seen > MAX_IMAGES:
                    evicted.append(e.get("path"))
                    continue
            keep.append(e)
        del keep[MAX_ENTRIES:]
        if not _write(keep):
            _remove_file(path)
            return None
        for old_path in evicted:
            _remove_file(old_path)
        return entry


def _remove_file(path):
    if not path:
        return True
    try:
        p = Path(path).resolve()
        if p.parent != IMAGE_DIR.resolve() or p.suffix.lower() != ".png":
            report_error(CLIPS_PATH, "נחסמה מחיקה של קובץ מחוץ לתיקיית התמונות")
            return False
        p.unlink(missing_ok=True)
        return True
    except OSError:
        report_error(path, "לא ניתן למחוק תמונה שמורה")
        return False


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
                return (removed, i) if _write(entries) else None
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
        return _write(entries)


def clear():
    """Clear the index before removing every managed PNG, including undo orphans."""
    with _lock:
        _read()
        if not _write([]):
            return False
        ok = True
        try:
            for path in IMAGE_DIR.glob("*.png"):
                ok = _remove_file(path) and ok
        except OSError:
            report_error(IMAGE_DIR)
            return False
        return ok


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
