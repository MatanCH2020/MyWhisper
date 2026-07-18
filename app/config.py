"""Load and access Mywishper configuration from config.json."""
import json
import logging
from pathlib import Path

log = logging.getLogger("config")

# config.json lives in the project root (one level above this app/ folder)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS = {
    "hotkey": "ctrl+space",
    "model": "ivrit-ai/whisper-large-v3-turbo-ct2",
    "language": "he",
    "device": "cuda",
    "compute_type": "float16",
    "beam_size": 5,
    "vad_filter": True,
    "restore_clipboard": True,
    "clipboard_restore_delay": 0.5,  # seconds; raise for apps slow to consume paste
    "max_record_seconds": 600,       # auto-stop cap for a forgotten recording; 0 = off
    "sounds": True,
    "sound_volume": 0.25,
    "initial_prompt": "",
    "highlight_unknown": True,
    "bidi_isolate": True,
    "theme": "dark",
}


def load_config():
    """Return the merged config dict (file values override defaults)."""
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read %s: %s. Using defaults.", CONFIG_PATH, e)
    return cfg


def save_config(cfg: dict):
    """Write the config dict back to config.json (preserves unknown keys)."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.error("Failed to write %s: %s", CONFIG_PATH, e)
