"""Load and access Mywishper configuration from config.json."""
import json
import logging
import math
from pathlib import Path

from safe_json import atomic_save, read_json, report_error

log = logging.getLogger("config")

# config.json lives in the project root (one level above this app/ folder)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS = {
    "hotkey": "ctrl+space",
    "model": "ivrit-ai/whisper-large-v3-turbo-ct2",
    "language": "he",
    "device": "cuda",
    "compute_type": "float16",
    "beam_size": 5,          # GPU: higher = more accurate
    "beam_size_cpu": 1,      # CPU: greedy decoding, ~2-3x faster
    "cpu_threads": 0,        # 0 = auto-detect
    "vad_filter": True,
    "input_device": "",  # microphone name; "" = system default
    "restore_clipboard": True,
    "clipboard_restore_delay": 0.5,  # seconds; raise for apps slow to consume paste
    "max_record_seconds": 600,       # auto-stop cap for a forgotten recording; 0 = off
    "idle_release_minutes": 10,      # free the model after N idle minutes; 0 = never
    "release_on_fullscreen": True,   # free the model while a fullscreen app (game) runs
    "sounds": True,
    "sound_volume": 0.25,
    "initial_prompt": "",
    "glossary_prompt": True,          # fold the English glossary into the prompt
    # Clipboard history — everything you copy, independent of transcription.
    "clipboard_history": True,
    "clipboard_hotkey": "ctrl+`",     # opens the picker
    "clipboard_paused": False,        # user-toggled pause (survives a restart)
    "highlight_unknown": True,
    "bidi_isolate": True,
    "theme": "dark",
    # Sign-in alone never opts a user into sending dictated text to OpenAI.
    "chatgpt_enabled": False,
    "chatgpt_model": "",
}

# Invalid values recover per key; unknown keys are preserved for compatibility.
_RANGES = {
    "beam_size": (1, 100), "beam_size_cpu": (1, 100), "cpu_threads": (0, 256),
    "max_record_seconds": (0, 86400), "idle_release_minutes": (0, 10080),
    "sound_volume": (0, 1), "clipboard_restore_delay": (0, 30),
}
_CHOICES = {
    "device": {"cuda", "cpu", "auto"}, "theme": {"dark", "light"},
    "compute_type": {"default", "auto", "int8", "int8_float16", "int8_float32",
                     "int8_bfloat16", "int16", "float16", "bfloat16", "float32"},
}


def _validate(key, value):
    if key not in DEFAULTS:
        return value
    default = DEFAULTS[key]
    ok = True
    if key in _RANGES:
        ok = (type(value) in (int, float) and _RANGES[key][0] <= value <= _RANGES[key][1]
              and math.isfinite(value)
              and (type(default) is float or float(value).is_integer()))
        if ok:
            value = type(default)(value)
    elif isinstance(default, bool):
        ok = type(value) is bool
    elif isinstance(default, str):
        ok = isinstance(value, str)
        if ok and key in _CHOICES:
            ok = value in _CHOICES[key]
        if ok and key in ("model", "language", "hotkey", "clipboard_hotkey"):
            ok = bool(value.strip())
    if not ok:
        report_error(CONFIG_PATH, f"הערך של {key} אינו תקין; נבחרה ברירת מחדל")
        return default
    return value


def load_config():
    cfg = dict(DEFAULTS)
    cfg.update(read_json(CONFIG_PATH, {}, lambda d: isinstance(d, dict)))
    return {key: _validate(key, value) for key, value in cfg.items()
            if not key.startswith("llm_")}


def save_config(cfg: dict) -> bool:
    if not isinstance(cfg, dict):
        report_error(CONFIG_PATH)
        return False
    return atomic_save(CONFIG_PATH, {k: _validate(k, v) for k, v in cfg.items() if not k.startswith("llm_")})
