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

# Two editing styles, both sent via /api/generate as a single directive prompt
# (NOT chat roles) — with system/user chat roles some models answer the
# instruction conversationally instead of editing; a single directive keeps them
# on task.
#
# "correct": conservative — fix clear errors only, never rephrase or change
# meaning, keep English terms in English, return only the corrected text.
_PROMPT_CORRECT = (
    "אתה עורך לשוני. לפניך טקסט קצר שהוכתב בעברית בהכתבה קולית.\n"
    "תקן אך ורק שגיאות כתיב, פיסוק ודקדוק ברורות.\n"
    "אל תנסח מחדש, אל תשנה את המשמעות, אל תוסיף ואל תשמיט תוכן, ואל תגיב לתוכן.\n"
    "שמור מונחים באנגלית באנגלית.\n"
    "החזר אך ורק את הטקסט המתוקן עצמו, בשורה אחת, בלי הסברים, בלי הקדמה ובלי מירכאות.\n\n"
    "הטקסט לתיקון:\n{t}"
)

# "rewrite": actively rephrase the dictated text into clean, professional
# writing — the GOAL is to improve the phrasing, not to leave it alone. Speech
# habits (repetitions, fillers, run-on "אז/כאילו/זאת אומרת", false starts) get
# cleaned; the constraint is that the meaning and all real content stay intact.
_PROMPT_REWRITE = (
    "אתה עורך לשוני מקצועי. לפניך טקסט קצר שהוכתב בעברית בהכתבה קולית, ולכן הוא "
    "מדובר ולא מלוטש.\n"
    "המשימה שלך: לשכתב אותו לעברית כתובה, ברורה ומקצועית — משפטים מסודרים, ניסוח "
    "רהוט, בלי סרבול.\n"
    "נקה חזרות, מילות מילוי וגמגומים אופייניים לדיבור (כמו \"אז\", \"כאילו\", "
    "\"זאת אומרת\", \"אמ\", והתחלות כפולות).\n"
    "חשוב: שמור בדיוק על אותה משמעות ועל כל התוכן — אל תמציא מידע, אל תוסיף רעיונות "
    "ואל תשמיט אף פרט; רק נסח את אותם הדברים בצורה טובה יותר.\n"
    "אל תגיב לתוכן. שמור מונחים באנגלית באנגלית.\n"
    "החזר אך ורק את הטקסט המשוכתב עצמו, בשורה אחת, בלי הסברים, בלי הקדמה ובלי מירכאות.\n\n"
    "הטקסט לשכתוב:\n{t}"
)

_PROMPTS = {"correct": _PROMPT_CORRECT, "rewrite": _PROMPT_REWRITE}

# Per-style decoding temperature. "correct" must be deterministic (greedy) so it
# only fixes clear errors; "rewrite" needs a little freedom or the model just
# echoes the input.
_TEMPS = {"correct": 0, "rewrite": 0.4}


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


def polish(text, model, url=DEFAULT_URL, timeout=20, style="correct"):
    """Return an LLM-edited version of *text*, or the original on any problem.

    *style* selects the prompt: "correct" (conservative error fixes only, the
    default) or "rewrite" (rephrase more professionally while staying close to
    the original). An unknown style falls back to "correct".

    Never raises: on any failure (server down, model missing, timeout, empty or
    suspiciously long output) the original text is returned unchanged.
    """
    if not text or not text.strip() or not model:
        return text
    t0 = time.perf_counter()
    try:
        out = _post(url, "/api/generate", {
            "model": model,
            "prompt": _PROMPTS.get(style, _PROMPT_CORRECT).format(t=text),
            "stream": False,
            "think": False,
            "options": {"temperature": _TEMPS.get(style, 0)},
        }, timeout)
        resp = out.get("response") or ""
        resp = _THINK_RE.sub("", resp).strip()
        # Drop an echoed label / surrounding quotes some models add.
        resp = re.sub(
            r"^\s*(?:הטקסט המתוקן|טקסט מתוקן|הטקסט לתיקון|"
            r"הטקסט המשוכתב|טקסט משוכתב|הטקסט לשכתוב)\s*:?\s*", "", resp)
        resp = resp.strip().strip('"').strip("'").strip()
        dt = time.perf_counter() - t0
        # Reject output that is empty, much longer than the input (the model
        # explained/rambled), or multi-line (likely commentary) — fall back to
        # the untouched text so a bad pass never corrupts the paste.
        if not resp or len(resp) > len(text) * 2 + 40 or "\n" in resp:
            log.info("LLM %s [%s]: output rejected (%.1fs) — kept original",
                     model, style, dt)
            return text
        log.info("LLM %s [%s]: %s (%.1fs)", model, style,
                 "changed text" if resp != text else "no change", dt)
        return resp
    except Exception as e:
        log.warning("LLM polish skipped (model=%s, %.1fs): %s",
                    model, time.perf_counter() - t0, e)
        return text
