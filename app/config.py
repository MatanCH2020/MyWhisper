"""Load and access Mywishper configuration from config.json."""
import json
from pathlib import Path

# config.json lives in the project root (one level above this app/ folder)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS = {
    "hotkey": "ctrl+alt+space",
    "model": "ivrit-ai/whisper-large-v3-turbo-ct2",
    "language": "he",
    "device": "cuda",
    "compute_type": "float16",
    "beam_size": 5,
    "vad_filter": True,
    "restore_clipboard": True,
    "sounds": True,
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
            print(f"[config] Failed to read {CONFIG_PATH}: {e}. Using defaults.")
    return cfg


def save_config(cfg: dict):
    """Write the config dict back to config.json (preserves unknown keys)."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[config] Failed to write {CONFIG_PATH}: {e}")
