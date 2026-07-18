"""Unit tests for the correction/learning layer.

Run from the project root:
    .\\.venv\\Scripts\\python -m unittest discover tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import corrections


class CorrectionsTestCase(unittest.TestCase):
    def setUp(self):
        # Point the module at a temp dir and reset its mtime caches so each
        # test starts from empty state.
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig_paths = (corrections.CORRECTIONS_PATH, corrections.DICTIONARY_PATH)
        corrections.CORRECTIONS_PATH = tmp / "corrections.json"
        corrections.DICTIONARY_PATH = tmp / "dictionary.json"
        corrections._corr_cache.update(mtime=-1.0, data={})
        corrections._dict_cache.update(mtime=-1.0, list=[], set=set())

    def tearDown(self):
        corrections.CORRECTIONS_PATH, corrections.DICTIONARY_PATH = self._orig_paths
        corrections._corr_cache.update(mtime=-1.0, data={})
        corrections._dict_cache.update(mtime=-1.0, list=[], set=set())
        self._tmp.cleanup()

    # ---- apply ----

    def test_apply_whole_word_only(self):
        corrections.add_correction("שלום", "צוהריים")
        self.assertEqual(corrections.apply("שלום לך"), "צוהריים לך")
        # Must not fire inside a larger word.
        self.assertEqual(corrections.apply("השלום נשמר"), "השלום נשמר")

    def test_apply_does_not_chain(self):
        corrections.add_correction("אבא", "בבא")
        corrections.add_correction("בבא", "גגא")
        # Single pass: each source word replaced once; the output of the first
        # replacement is never re-matched by the second.
        self.assertEqual(corrections.apply("אבא בבא"), "בבא גגא")

    def test_apply_longest_key_wins(self):
        corrections.add_correction("וייס פר", "וויספר")
        corrections.add_correction("פר", "פרה")
        self.assertEqual(corrections.apply("וייס פר אמר"), "וויספר אמר")

    def test_apply_empty_and_no_corrections(self):
        self.assertEqual(corrections.apply(""), "")
        self.assertEqual(corrections.apply("טקסט רגיל"), "טקסט רגיל")

    # ---- learning / dictionary ----

    def test_add_correction_approves_target(self):
        corrections.add_correction("תאמנל", "thumbnail")
        self.assertIn("thumbnail", corrections._dictionary_set())

    def test_approved_word_never_flagged(self):
        corrections.approve_word("זזזזז")
        tokens = corrections.flag_tokens("זזזזז")
        words = [t for t in tokens if t["word"]]
        self.assertEqual(len(words), 1)
        self.assertFalse(words[0]["unknown"])

    def test_dictionary_preserves_insertion_order(self):
        for w in ("צצצא", "אאאצ", "בבבצ"):
            corrections.approve_word(w)
        self.assertEqual(corrections._load_dictionary(), ["צצצא", "אאאצ", "בבבצ"])

    def test_bias_terms_prioritizes_corrections(self):
        # Overflow the cap with dictionary words; corrections must survive.
        for i in range(corrections._MAX_BIAS_TERMS + 20):
            corrections.approve_word("מלה" + "א" * (i + 1))
        corrections.add_correction("שגוי", "נכון")
        terms = corrections.bias_terms().split()
        self.assertIn("נכון", terms)
        self.assertLessEqual(len(terms), corrections._MAX_BIAS_TERMS)
        self.assertEqual(terms[0], "נכון")  # corrections come first

    def test_remove_correction(self):
        corrections.add_correction("אבג", "דהו")
        corrections.remove_correction("אבג")
        self.assertEqual(corrections.list_corrections(), {})

    # ---- tokenization / bidi ----

    def test_flag_tokens_roundtrip(self):
        text = "שלום world, מה קורה?"
        tokens = corrections.flag_tokens(text)
        self.assertEqual("".join(t["text"] for t in tokens), text)
        self.assertEqual([t["text"] for t in tokens if t["word"]],
                         ["שלום", "מה", "קורה"])

    def test_format_bidi_wraps_latin_inside_hebrew(self):
        out = corrections.format_bidi("תעשה render עכשיו")
        self.assertIn(f"{corrections._LRI}render{corrections._PDI}", out)

    def test_format_bidi_noop_for_pure_english(self):
        self.assertEqual(corrections.format_bidi("pure english"), "pure english")

    def test_format_bidi_idempotent(self):
        once = corrections.format_bidi("קובץ mp4 חדש")
        self.assertEqual(corrections.format_bidi(once), once)


if __name__ == "__main__":
    unittest.main()
