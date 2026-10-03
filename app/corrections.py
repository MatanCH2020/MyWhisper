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
import difflib
import json
import logging
import re
import threading
from functools import lru_cache
from pathlib import Path

from safe_json import atomic_save, read_json

log = logging.getLogger("corrections")

_ROOT = Path(__file__).resolve().parent.parent
CORRECTIONS_PATH = _ROOT / "corrections.json"
DICTIONARY_PATH = _ROOT / "dictionary.json"
ENGLISH_TERMS_PATH = _ROOT / "english_terms.json"

# Shipped default English/tech terms so mixed Hebrew/English dictation works out
# of the box. The user can add or remove terms from the מילון page; the file is
# seeded from this list on first load (when english_terms.json does not exist).
_DEFAULT_ENGLISH_TERMS = [
    "PowerShell", "GitHub", "Git", "Docker", "Python", "Node", "npm",
    "API", "JSON", "VSCode", "Linux", "Windows", "Claude", "Cursor",
    "Anthropic", "Whisper", "CUDA", "GPU", "README", "commit", "terminal",
    "framework", "prompt", "token", "endpoint",
]

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
_wordlist_cache = None  # lazily built Hebrew word list for suggestions


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
_dict_cache = {"mtime": -1.0, "list": [], "set": set()}
_eng_cache = {"mtime": -1.0, "list": []}


def _file_stamp(path):
    try:
        stat = path.stat()
        return (str(path.absolute()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(path.absolute()), None, None)


def _load_corrections() -> dict:
    stamp = _file_stamp(CORRECTIONS_PATH)
    if stamp != _corr_cache["mtime"] or stamp[1] is None:
        _corr_cache["data"] = read_json(CORRECTIONS_PATH, {}, lambda d:
            isinstance(d, dict) and all(isinstance(k, str) and isinstance(v, str)
                                       for k, v in d.items()))
        _corr_cache["mtime"] = stamp
    return _corr_cache["data"]


_lock = threading.Lock()


def _save_corrections(data: dict):
    saved = atomic_save(CORRECTIONS_PATH, data)
    if saved:
        _corr_cache["mtime"] = -1.0
    return saved


def _load_dictionary() -> list:
    """Approved words, oldest first; failed reads must not permit overwrites."""
    stamp = _file_stamp(DICTIONARY_PATH)
    if stamp != _dict_cache["mtime"] or stamp[1] is None:
        words = read_json(DICTIONARY_PATH, [], lambda d:
            isinstance(d, list) and all(isinstance(w, str) for w in d))
        _dict_cache.update(list=words, set=set(words), mtime=stamp)
    return _dict_cache["list"]


def _dictionary_set() -> set:
    """The approved words as a set, for O(1) membership checks."""
    _load_dictionary()
    return _dict_cache["set"]


def _save_dictionary(words: list):
    """Persist the dictionary, preserving insertion order (newest last) so
    bias_terms() can favor recently approved words."""
    saved = atomic_save(DICTIONARY_PATH, words)
    if saved:
        _dict_cache["mtime"] = -1.0
    return saved


def _save_english_terms(terms: list):
    saved = atomic_save(ENGLISH_TERMS_PATH, terms)
    if saved:
        _eng_cache["mtime"] = -1.0
    return saved


def _load_english_terms() -> list:
    """Seed a missing glossary, while preserving corrupt or unreadable originals."""
    stamp = _file_stamp(ENGLISH_TERMS_PATH)
    if stamp != _eng_cache["mtime"] or stamp[1] is None:
        terms = read_json(ENGLISH_TERMS_PATH, list(_DEFAULT_ENGLISH_TERMS), lambda d:
            isinstance(d, list) and all(isinstance(w, str) for w in d))
        if stamp[1] is None:
            _save_english_terms(terms)  # read_json blocks this if the file was unreadable
            stamp = _file_stamp(ENGLISH_TERMS_PATH)
        _eng_cache.update(list=terms, mtime=stamp)
    return _eng_cache["list"]


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
        approved = _dictionary_set()
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
    approved = _dictionary_set()
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
    Hebrew words only (so a correction never fires inside a larger word).

    All corrections run in a single pass (one alternation regex, longest keys
    first), so the output of one replacement can never be re-matched by another
    (no A->B, B->C chaining into A->C)."""
    if not text:
        return text
    with _lock:
        corr = dict(_load_corrections())
    if not corr:
        return text
    alternation = "|".join(
        re.escape(wrong) for wrong in sorted(corr, key=len, reverse=True))
    pattern = re.compile(
        f"(?<![{_HEB_LETTER}])(?:{alternation})(?![{_HEB_LETTER}])")
    return pattern.sub(lambda m: corr[m.group()], text)


def add_correction(wrong: str, right: str):
    """Record wrong->right and treat the corrected word as approved vocabulary."""
    wrong, right = _normalize(wrong), _normalize(right)
    if not wrong or not right or wrong == right:
        return
    with _lock:
        corr = dict(_load_corrections())
        corr[wrong] = right
        if not _save_corrections(corr):
            return False
    return approve_word(right)


def approve_word(word: str):
    """Mark a word as correct so it is never flagged again and biases Whisper."""
    w = _normalize(word)
    if not w:
        return
    with _lock:
        if w not in _dictionary_set():
            return _save_dictionary(_load_dictionary() + [w])
        return True


def list_corrections() -> dict:
    """Return the current {wrong: right} map (for the management UI)."""
    with _lock:
        return dict(_load_corrections())


def learn_scan(pairs, terms):
    """Add AI rules without replacing manual rules. Return exactly what was added.

    Targets are already known via the correction map, so no separate dictionary
    approval is needed; this also makes undo precise.
    """
    with _lock:
        current = dict(_load_corrections())
        added = {wrong: right for wrong, right in pairs.items() if wrong not in current}
        saved = True
        if added and not _save_corrections({**current, **added}):
            added = {}
            saved = False
        existing = list(_load_english_terms())
        lower = {term.lower() for term in existing}
        new_terms = []
        for term in terms:
            if term.lower() not in lower:
                lower.add(term.lower())
                new_terms.append(term)
        if new_terms and not _save_english_terms(existing + new_terms):
            new_terms = []
            saved = False
        return added, new_terms, saved


def undo_scan_learning(pairs, terms):
    """Remove the scan's additions only if their values have not been changed."""
    with _lock:
        current = dict(_load_corrections())
        removed = {wrong for wrong, right in pairs.items() if current.get(wrong) == right}
        if removed and not _save_corrections({k: v for k, v in current.items() if k not in removed}):
            return False
        existing = list(_load_english_terms())
        kept = [term for term in existing if term not in terms]
        return _save_english_terms(kept) if kept != existing else True


def remove_correction(wrong: str):
    """Forget a learned correction."""
    with _lock:
        corr = dict(_load_corrections())
        if wrong in corr:
            del corr[wrong]
            return _save_corrections(corr)
        return True


# ---------------- English glossary ----------------

def english_terms() -> list:
    """The user's English/tech glossary (seeded with defaults on first use)."""
    with _lock:
        return list(_load_english_terms())


def add_english_term(term: str):
    """Add an English term to the glossary (case-insensitive de-dupe, newest last)."""
    term = (term or "").strip()
    if not term:
        return
    with _lock:
        terms = list(_load_english_terms())
        if any(term.lower() == t.lower() for t in terms):
            return
        return _save_english_terms(terms + [term])


def remove_english_term(term: str):
    """Remove an English term from the glossary (case-insensitive)."""
    with _lock:
        terms = _load_english_terms()
        kept = [t for t in terms if t.lower() != (term or "").strip().lower()]
        if len(kept) != len(terms):
            return _save_english_terms(kept)
        return True


def bias_terms() -> str:
    """Space-joined string of glossary/corrected/approved words to bias Whisper toward.

    English glossary terms come first (highest signal for mixed dictation), then
    correction targets — neither is dropped in favor of plain dictionary words;
    the most recently approved dictionary words fill the remaining slots, so the
    prompt stays bounded at _MAX_BIAS_TERMS.
    """
    with _lock:
        eng_terms = list(dict.fromkeys(_load_english_terms()))[:_MAX_BIAS_TERMS]
        seen = set(eng_terms)
        corr_terms = [v for v in dict.fromkeys(_load_corrections().values())
                      if v not in seen][:max(0, _MAX_BIAS_TERMS - len(eng_terms))]
        seen.update(corr_terms)
        dict_terms = [w for w in _load_dictionary() if w not in seen]
    remaining = _MAX_BIAS_TERMS - len(eng_terms) - len(corr_terms)
    terms = eng_terms + corr_terms + (dict_terms[-remaining:] if remaining > 0 else [])
    return " ".join(terms)


def _hebrew_wordlist() -> list:
    """Return a large Hebrew word list for fuzzy suggestion matching.

    Lazily loads wordfreq's top 50,000 Hebrew words and merges in the user's
    approved dictionary and correction targets. Cached at module level so the
    list is built only once per session.
    """
    global _wordlist_cache
    if _wordlist_cache is not None:
        return _wordlist_cache
    words = set()
    try:
        from wordfreq import top_n_list
        words.update(top_n_list("he", 50000))
    except Exception:
        pass  # wordfreq unavailable — fall back to user vocabulary only
    words.update(_dictionary_set())
    words.update(_load_corrections().values())
    _wordlist_cache = list(words)
    return _wordlist_cache


def suggest_similar(word: str, n: int = 5) -> list:
    """Suggest Hebrew words similar to *word* from the dictionary.

    Uses difflib.get_close_matches against a cached 50K-word Hebrew list
    (from wordfreq) plus the user's approved vocabulary and correction targets.
    Returns up to *n* suggestions, excluding the word itself and known-bad
    words (keys in corrections.json).
    """
    w = _normalize(word)
    if len(w) < 2:
        return []
    wordlist = _hebrew_wordlist()
    if not wordlist:
        return []
    matches = difflib.get_close_matches(w, wordlist, n=n + 5, cutoff=0.6)
    # Filter out the word itself and words that are known-wrong correction keys.
    bad_keys = set(_load_corrections().keys())
    return [m for m in matches if m != w and m not in bad_keys][:n]


def format_bidi(text: str) -> str:
    """Wrap Latin (English/loanword) runs in directional isolates so they render
    left-to-right inside right-to-left Hebrew, without affecting the Hebrew flow.

    No-op for text that has no Hebrew (pure English), or that is already wrapped.
    """
    if not text or _LRI in text or not _HEB_ANY.search(text):
        return text
    return _LATIN_RUN.sub(lambda m: f"{_LRI}{m.group()}{_PDI}", text)
