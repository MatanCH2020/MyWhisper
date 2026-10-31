"""Load and access Mywishper configuration from config.json."""
import json
import logging
import math
from pathlib import Path
from platform_support import data_dir, log_dir

from platform_support import IS_MAC, defaults as platform_defaults

from safe_json import atomic_save, read_json, report_error

log = logging.getLogger("config")

# config.json lives in the project root (one level above this app/ folder)
CONFIG_PATH = data_dir() / "config.json"

DEFAULTS = {
    "backend": "faster-whisper",
    "hotkey": "ctrl+space",
    "model": "ivrit-ai/whisper-large-v3-ct2",
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
    "glossary_prompt": True,          # prioritize English vocabulary in bounded hints
    # Unified picker — copied text/images and completed transcriptions.
    "clipboard_history": True,
    "clipboard_hotkey": "ctrl+`",     # opens the picker
    "unified_history_imported": False, # one-time legacy transcription import
    "clipboard_paused": False,        # user-toggled pause (survives a restart)
    "highlight_unknown": True,
    "bidi_isolate": True,
    "theme": "dark",
    "restart_after_update": True,    # Windows updater; independent of login startup
    # Sign-in alone never opts a user into sending dictated text to OpenAI.
    "chatgpt_enabled": False,
    "chatgpt_model": "",
    "chatgpt_browser": "system",
}

DEFAULTS.update(platform_defaults())

# Invalid values recover per key; unknown keys are preserved for compatibility.
_RANGES = {
    "beam_size": (1, 100), "beam_size_cpu": (1, 100), "cpu_threads": (0, 256),
    "max_record_seconds": (0, 86400), "idle_release_minutes": (0, 10080),
    "sound_volume": (0, 1), "clipboard_restore_delay": (0, 30),
}
_CHOICES = {
    "chatgpt_browser": {"system", "chrome", "edge", "firefox", "brave", "opera", "vivaldi"},
    "backend": {"faster-whisper", "mlx"},
    "device": {"cuda", "cpu", "auto", "metal"}, "theme": {"dark", "light"},
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
    if IS_MAC:
        cfg["chatgpt_enabled"] = False
    return {key: _validate(key, value) for key, value in cfg.items()
            if not key.startswith("llm_")}


def save_config(cfg: dict) -> bool:
    if not isinstance(cfg, dict):
        report_error(CONFIG_PATH)
        return False
    return atomic_save(CONFIG_PATH, {k: _validate(k, v) for k, v in cfg.items() if not k.startswith("llm_")})
