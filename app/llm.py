"""Optional local-LLM polish via Ollama (http://localhost:11434) — OFF by default.

When enabled in Settings, a transcription is sent to a locally running Ollama
model for a conservative Hebrew spelling/grammar pass before it is pasted.
Everything stays on the machine — no cloud, no API keys.

The layer *fails open*: any problem (Ollama not running, model missing, timeout,
empty or rambling output) returns the ORIGINAL text, so enabling it can never
block a paste or replace the text with garbage. It is meant for users with
capable hardware who opt in explicitly.
"""
import json
import logging
import re
import time
import urllib.request

log = logging.getLogger("llm")

DEFAULT_URL = "http://localhost:11434"

# Some models (qwen3, gemma with thinking) emit a <think>…</think> preamble.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Conservative editor instruction: fix clear errors only, never rephrase or
# change meaning, keep English terms in English, return only the corrected text.
# Sent via /api/generate as a single prompt (NOT chat roles) — with system/user
# chat roles some models answer the instruction conversationally instead of
# editing; a single directive prompt keeps them on task.
_PROMPT = (
    "אתה עורך לשוני. לפניך טקסט קצר שהוכתב בעברית בהכתבה קולית.\n"
    "תקן אך ורק שגיאות כתיב, פיסוק ודקדוק ברורות.\n"
    "אל תנסח מחדש, אל תשנה את המשמעות, אל תוסיף ואל תשמיט תוכן, ואל תגיב לתוכן.\n"
    "שמור מונחים באנגלית באנגלית.\n"
    "החזר אך ורק את הטקסט המתוקן עצמו, בשורה אחת, בלי הסברים, בלי הקדמה ובלי מירכאות.\n\n"
    "הטקסט לתיקון:\n{t}"
)


def _get(url, path, timeout):
    req = urllib.request.Request(url.rstrip("/") + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _post(url, path, payload, timeout):
    req = urllib.request.Request(
        url.rstrip("/") + path, json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def list_models(url=DEFAULT_URL, timeout=3):
    """Names of models installed in the local Ollama, or [] if unreachable."""
    try:
        data = _get(url, "/api/tags", timeout)
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))
    except Exception:
        return []


def available(url=DEFAULT_URL, timeout=2):
    """True if a local Ollama server answers on *url*."""
    try:
        _get(url, "/api/tags", timeout)
        return True
    except Exception:
        return False


def polish(text, model, url=DEFAULT_URL, timeout=20):
    """Return an LLM-corrected version of *text*, or the original on any problem.

    Never raises: on any failure (server down, model missing, timeout, empty or
    suspiciously long output) the original text is returned unchanged.
    """
    if not text or not text.strip() or not model:
        return text
    t0 = time.perf_counter()
    try:
        out = _post(url, "/api/generate", {
            "model": model,
            "prompt": _PROMPT.format(t=text),
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }, timeout)
        resp = out.get("response") or ""
        resp = _THINK_RE.sub("", resp).strip()
        # Drop an echoed label / surrounding quotes some models add.
        resp = re.sub(r"^\s*(?:הטקסט המתוקן|טקסט מתוקן|הטקסט לתיקון)\s*:?\s*", "", resp)
        resp = resp.strip().strip('"').strip("'").strip()
        dt = time.perf_counter() - t0
        # Reject output that is empty, much longer than the input (the model
        # explained/rambled), or multi-line (likely commentary) — fall back to
        # the untouched text so a bad pass never corrupts the paste.
        if not resp or len(resp) > len(text) * 2 + 40 or "\n" in resp:
            log.info("LLM %s: output rejected (%.1fs) — kept original", model, dt)
            return text
        log.info("LLM %s: %s (%.1fs)", model,
                 "changed text" if resp != text else "no change", dt)
        return resp
    except Exception as e:
        log.warning("LLM polish skipped (model=%s, %.1fs): %s",
                    model, time.perf_counter() - t0, e)
        return text
