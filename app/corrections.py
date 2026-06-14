"""Self-improving correction layer for Mywishper.

Stores two JSON files next to history.json in the project root:
  - corrections.json : {wrong_word: right_word}  — applied to every new transcription.
  - dictionary.json  : [approved_word, ...]       — user-approved words, never flagged.

The "learning" loop: the user fixes a mis-transcribed word once; from then on the
fix is (a) auto-applied to future transcriptions via apply(), and (b) fed back to
Whisper as bias terms via bias_terms() so the model is nudged to produce it correctly.

Unknown-word detection uses the wordfreq Hebrew lexicon (offline). If wordfreq is
not installed the feature degrades gracefully: nothing is flagged.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
CORRECTIONS_PATH = _ROOT / "corrections.json"
DICTIONARY_PATH = _ROOT / "dictionary.json"

# Hebrew letters (alef..tav, incl. final forms); points/cantillation are stripped.
_HEB_LETTER = "א-ת"
_WORD_RE = re.compile(f"[{_HEB_LETTER}]+")
_POINTS_RE = re.compile("[֑-ׇ]")  # niqqud + cantillation marks

# Bidi: wrap Latin (loanword) runs in directional isolates so English stays LTR
# inside RTL Hebrew text without disturbing the surrounding direction.
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9'._\-&/]*")
_HEB_ANY = re.compile(f"[{_HEB_LETTER}]")
_LRI = "⁦"  # LEFT-TO-RIGHT ISOLATE
_PDI = "⁩"  # POP DIRECTIONAL ISOLATE
# Single-letter attachable prefixes (ו ה ב כ ל מ ש), used to reduce false flags.
_PREFIXES = set("ובכלמהש")

# Bias prompt is capped so it never balloons the Whisper prompt unboundedly.
_MAX_BIAS_TERMS = 100

_wordfreq_fn = False  # False = not yet probed; None = unavailable; else callable


def _zipf():
    """Return wordfreq.zipf_frequency (cached), or None if wordfreq is unavailable."""
    global _wordfreq_fn
    if _wordfreq_fn is False:
        try:
            from wordfreq import zipf_frequency
            _wordfreq_fn = zipf_frequency
        except Exception:
            _wordfreq_fn = None
    return _wordfreq_fn


# ---------------- persistence ----------------

# Small mtime-keyed caches so rendering 100s of history cards doesn't re-read and
# re-parse the JSON files once per word.
_corr_cache = {"mtime": -1.0, "data": {}}
_dict_cache = {"mtime": -1.0, "data": set()}


def _load_corrections() -> dict:
    try:
        mtime = CORRECTIONS_PATH.stat().st_mtime
    except OSError:
        return {}
    if mtime != _corr_cache["mtime"]:
        try:
            with open(CORRECTIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _corr_cache["data"] = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            _corr_cache["data"] = {}
        _corr_cache["mtime"] = mtime
    return _corr_cache["data"]


def _save_corrections(data: dict):
    try:
        with open(CORRECTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[corrections] Failed to write: {e}")


def _load_dictionary() -> set:
    try:
        mtime = DICTIONARY_PATH.stat().st_mtime
    except OSError:
        return set()
    if mtime != _dict_cache["mtime"]:
        try:
            with open(DICTIONARY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _dict_cache["data"] = set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            _dict_cache["data"] = set()
        _dict_cache["mtime"] = mtime
    return _dict_cache["data"]


def _save_dictionary(words: set):
    try:
        with open(DICTIONARY_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(words), f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[corrections] Failed to write: {e}")


# ---------------- helpers ----------------

def _normalize(word: str) -> str:
    """Strip Hebrew points so lookups match wordfreq's normalized forms."""
    return _POINTS_RE.sub("", (word or "")).strip()


def _prefix_variants(word: str):
    """Yield the word plus forms with 1-2 leading Hebrew prefixes removed."""
    yield word
    if len(word) > 2 and word[0] in _PREFIXES:
        yield word[1:]
        if len(word) > 3 and word[1] in _PREFIXES:
            yield word[2:]


# ---------------- public API ----------------

@lru_cache(maxsize=50000)
def _in_lexicon(word: str) -> bool:
    """Cached wordfreq lookup (the lexicon is static, so memoizing is safe and
    makes flagging hundreds of history cards fast)."""
    zipf = _zipf()
    if zipf is None:
        return True  # lexicon unavailable -> never flag
    for variant in _prefix_variants(word):
        if zipf(variant, "he") > 0:
            return True
    return False


def is_known(word: str, approved: set = None, targets: set = None) -> bool:
    """True if the Hebrew word is recognised (approved, a correction target, or in
    the wordfreq lexicon — also trying prefix-stripped forms)."""
    w = _normalize(word)
    if len(w) < 2:
        return True  # don't flag single letters
    if approved is None:
        approved = _load_dictionary()
    if targets is None:
        targets = set(_load_corrections().values())
    if w in approved or w in targets:
        return True
    return _in_lexicon(w)


def flag_tokens(text: str):
    """Split text into ordered tokens for the UI.

    Returns a list of dicts: {"text": str, "word": bool, "unknown": bool}.
    Only Hebrew-letter runs are words (clickable); unknown=True marks ones to
    highlight. Separators (spaces/punctuation/other scripts) come back as word=False.
    """
    approved = _load_dictionary()
    targets = set(_load_corrections().values())
    tokens = []
    pos = 0
    for m in _WORD_RE.finditer(text or ""):
        if m.start() > pos:
            tokens.append({"text": text[pos:m.start()], "word": False, "unknown": False})
        w = m.group()
        tokens.append({"text": w, "word": True,
                       "unknown": not is_known(w, approved, targets)})
        pos = m.end()
    if pos < len(text or ""):
        tokens.append({"text": text[pos:], "word": False, "unknown": False})
    return tokens


def apply(text: str) -> str:
    """Apply the learned {wrong: right} map to a transcription, matching whole
    Hebrew words only (so a correction never fires inside a larger word)."""
    if not text:
        return text
    corr = _load_corrections()
    if not corr:
        return text
    # Longest keys first so multi-word / longer corrections win over shorter ones.
    for wrong in sorted(corr, key=len, reverse=True):
        right = corr[wrong]
        pattern = re.compile(
            f"(?<![{_HEB_LETTER}]){re.escape(wrong)}(?![{_HEB_LETTER}])")
        text = pattern.sub(right, text)
    return text


def add_correction(wrong: str, right: str):
    """Record wrong->right and treat the corrected word as approved vocabulary."""
    wrong, right = _normalize(wrong), _normalize(right)
    if not wrong or not right or wrong == right:
        return
    corr = _load_corrections()
    corr[wrong] = right
    _save_corrections(corr)
    approve_word(right)


def approve_word(word: str):
    """Mark a word as correct so it is never flagged again and biases Whisper."""
    w = _normalize(word)
    if not w:
        return
    words = _load_dictionary()
    if w not in words:
        words.add(w)
        _save_dictionary(words)


def list_corrections() -> dict:
    """Return the current {wrong: right} map (for the management UI)."""
    return _load_corrections()


def remove_correction(wrong: str):
    """Forget a learned correction."""
    corr = _load_corrections()
    if wrong in corr:
        del corr[wrong]
        _save_corrections(corr)


def bias_terms() -> str:
    """Space-joined string of corrected/approved words to bias Whisper toward.

    Capped at the most recent _MAX_BIAS_TERMS so the prompt stays bounded.
    """
    terms = list(dict.fromkeys(list(_load_corrections().values()) + sorted(_load_dictionary())))
    if not terms:
        return ""
    return " ".join(terms[-_MAX_BIAS_TERMS:])


def format_bidi(text: str) -> str:
    """Wrap Latin (English/loanword) runs in directional isolates so they render
    left-to-right inside right-to-left Hebrew, without affecting the Hebrew flow.

    No-op for text that has no Hebrew (pure English), or that is already wrapped.
    """
    if not text or _LRI in text or not _HEB_ANY.search(text):
        return text
    return _LATIN_RUN.sub(lambda m: f"{_LRI}{m.group()}{_PDI}", text)
