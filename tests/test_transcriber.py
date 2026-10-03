"""Mixed dictation decoding contracts without a model download or GPU."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from transcriber import Transcriber


class MixedDictationTest(unittest.TestCase):
    def transcriber(self, **settings):
        tr = Transcriber(dict(language="he", model="test", **settings))
        tr.model = Mock(max_length=448)
        # A deterministic tokenizer: UTF-8 bytes approximate multi-token Hebrew
        # and make bounds verifiable without fetching production weights.
        tr.model.hf_tokenizer.encode.side_effect = lambda text: SimpleNamespace(ids=list(text.encode("utf-8")))
        tr.model.transcribe.return_value = (iter([SimpleNamespace(text="אני בונה אפליקציה ל-iOS ול-Android.")]), None)
        return tr

    def test_glossary_uses_one_channel_and_mixed_text_survives(self):
        tr = self.transcriber(initial_prompt="שלום.")
        text = tr.transcribe(np.ones(160, dtype=np.float32),
                            hotwords="GitHub iOS Android", glossary=["GitHub", "iOS", "Android"])
        self.assertEqual(text, "אני בונה אפליקציה ל-iOS ול-Android.")
        args = tr.model.transcribe.call_args.kwargs
        self.assertEqual(args["initial_prompt"], "שלום.")
        self.assertEqual(args["hotwords"].split(), ["Android", "iOS", "GitHub"])
        self.assertEqual(args["task"], "transcribe")
        self.assertEqual(args["language"], "he")
        self.assertFalse(args["condition_on_previous_text"])

    def test_large_dictionary_respects_shared_token_budget_and_new_terms(self):
        tr = self.transcriber(initial_prompt="שלום " * 100)
        glossary = [f"OldTerm{i}" for i in range(200)] + ["iOS", "Android"]
        prompt, vocabulary = tr._prompt_inputs(" ".join(glossary), glossary)
        size = sum(len(tr.model.hf_tokenizer.encode(" " + value).ids) for value in (prompt, vocabulary) if value)
        self.assertLessEqual(size, 112)
        self.assertLessEqual(len(tr.model.hf_tokenizer.encode(" " + prompt).ids), 16)
        self.assertIn("iOS", vocabulary.split())
        self.assertIn("Android", vocabulary.split())

    def test_glossary_switch_off_keeps_explicit_hotwords_without_duplication(self):
        tr = self.transcriber(glossary_prompt=False)
        prompt, vocabulary = tr._prompt_inputs("Python Python Android", ["iOS"])
        self.assertIsNone(prompt)
        self.assertEqual(vocabulary, "Python Android")

    def test_cpu_beam_setting_is_preserved(self):
        tr = self.transcriber(beam_size=5, beam_size_cpu=1)
        tr.device = "cpu"
        tr.transcribe(np.ones(160, dtype=np.float32))
        self.assertEqual(tr.model.transcribe.call_args.kwargs["beam_size"], 1)

    def test_empty_audio_does_not_load_or_decode(self):
        tr = self.transcriber()
        self.assertEqual(tr.transcribe(np.array([], dtype=np.float32)), "")
        tr.model.transcribe.assert_not_called()

    def test_budget_does_not_cut_a_word(self):
        tr = self.transcriber()
        self.assertEqual(tr._fit_hint("iOS Android Python", 12), "iOS Android")
