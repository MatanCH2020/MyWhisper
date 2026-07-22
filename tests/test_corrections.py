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
        self._orig_paths = (corrections.CORRECTIONS_PATH, corrections.DICTIONARY_PATH,
                            corrections.ENGLISH_TERMS_PATH)
        corrections.CORRECTIONS_PATH = tmp / "corrections.json"
        corrections.DICTIONARY_PATH = tmp / "dictionary.json"
        corrections.ENGLISH_TERMS_PATH = tmp / "english_terms.json"
        corrections._corr_cache.update(mtime=-1.0, data={})
        corrections._dict_cache.update(mtime=-1.0, list=[], set=set())
        corrections._eng_cache.update(mtime=-1.0, list=[])

    def tearDown(self):
        (corrections.CORRECTIONS_PATH, corrections.DICTIONARY_PATH,
         corrections.ENGLISH_TERMS_PATH) = self._orig_paths
        corrections._corr_cache.update(mtime=-1.0, data={})
        corrections._dict_cache.update(mtime=-1.0, list=[], set=set())
        corrections._eng_cache.update(mtime=-1.0, list=[])
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
        # Overflow the cap with dictionary words; corrections must survive, and
        # the English glossary leads the bias string.
        for i in range(corrections._MAX_BIAS_TERMS + 20):
            corrections.approve_word("מלה" + "א" * (i + 1))
        corrections.add_correction("שגוי", "נכון")
        terms = corrections.bias_terms().split()
        self.assertIn("נכון", terms)  # correction target survives the cap
        self.assertLessEqual(len(terms), corrections._MAX_BIAS_TERMS)
        self.assertIn(terms[0], corrections.english_terms())  # English first

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

    # ---- suggest_similar ----

    def test_suggest_similar_returns_list(self):
        result = corrections.suggest_similar("שלומ")
        self.assertIsInstance(result, list)

    def test_suggest_similar_excludes_self(self):
        # If the exact word exists in the wordlist, it must not appear as its
        # own suggestion.
        result = corrections.suggest_similar("שלום")
        self.assertNotIn("שלום", result)

    def test_suggest_similar_excludes_bad_keys(self):
        corrections.add_correction("שגוי", "נכון")
        result = corrections.suggest_similar("שגוי")
        # "שגוי" is a known-bad correction key and must not be suggested.
        self.assertNotIn("שגוי", result)

    def test_suggest_similar_short_word(self):
        self.assertEqual(corrections.suggest_similar("א"), [])

    def test_suggest_similar_empty(self):
        self.assertEqual(corrections.suggest_similar(""), [])

    def test_suggest_similar_max_results(self):
        result = corrections.suggest_similar("שלומ", n=3)
        self.assertLessEqual(len(result), 3)

    def test_suggest_similar_includes_user_vocabulary(self):
        # Approve a custom word and invalidate the wordlist cache so it picks
        # up the new vocabulary.
        corrections.approve_word("זזזזזזזזזזז")
        corrections._wordlist_cache = None  # force rebuild
        result = corrections.suggest_similar("זזזזזזזזזזא")
        # The approved word should be close enough to appear.
        self.assertIn("זזזזזזזזזזז", result)

    # ---- English glossary ----

    def test_english_terms_seeded_on_first_load(self):
        # File does not exist yet -> defaults are written and returned.
        terms = corrections.english_terms()
        self.assertIn("PowerShell", terms)
        self.assertIn("GitHub", terms)
        self.assertTrue(corrections.ENGLISH_TERMS_PATH.exists())

    def test_add_english_term(self):
        corrections.english_terms()  # seed first
        corrections.add_english_term("Kubernetes")
        self.assertIn("Kubernetes", corrections.english_terms())

    def test_add_english_term_dedupes_case_insensitive(self):
        corrections.add_english_term("GitHub")
        corrections.add_english_term("github")
        terms = corrections.english_terms()
        self.assertEqual(sum(1 for t in terms if t.lower() == "github"), 1)

    def test_remove_english_term(self):
        corrections.english_terms()
        corrections.remove_english_term("PowerShell")
        self.assertNotIn("PowerShell", corrections.english_terms())

    def test_bias_terms_includes_english_first(self):
        corrections.add_correction("פאוורשל", "PowerShell")
        bias = corrections.bias_terms().split()
        self.assertIn("GitHub", bias)          # a seeded English term
        self.assertIn("PowerShell", bias)      # a correction target
        # English glossary terms lead the bias string.
        self.assertLess(bias.index("GitHub"), len(bias))

    def test_bias_terms_bounded(self):
        for i in range(200):
            corrections.add_english_term(f"Term{i}")
        self.assertLessEqual(len(corrections.bias_terms().split()),
                             corrections._MAX_BIAS_TERMS)

    # ---- multi-word / English correction backstop ----

    def test_apply_multiword_phrase_to_english(self):
        corrections.add_correction("פאוור של", "PowerShell")
        self.assertEqual(corrections.apply("תעדכן את פאוור של עכשיו"),
                         "תעדכן את PowerShell עכשיו")

    def test_add_correction_accepts_english_value(self):
        corrections.add_correction("גיטהאב", "GitHub")
        self.assertEqual(corrections.apply("פתח גיטהאב"), "פתח GitHub")


if __name__ == "__main__":
    unittest.main()
