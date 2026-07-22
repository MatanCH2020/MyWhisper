"""Unit tests for the optional local-LLM polish layer.

These cover the *fail-open* safety contract without requiring a running Ollama:
any problem must return the original text and never raise.

Run from the project root:
    .\\.venv\\Scripts\\python -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import llm

# An unreachable endpoint: port 9 (discard) refuses fast -> exercises fail-open.
_DEAD = "http://localhost:9"


class LlmFailOpenTestCase(unittest.TestCase):
    def test_no_model_returns_original(self):
        self.assertEqual(llm.polish("שלום עולם", ""), "שלום עולם")

    def test_empty_text_returns_empty(self):
        self.assertEqual(llm.polish("", "any-model"), "")

    def test_whitespace_text_unchanged(self):
        self.assertEqual(llm.polish("   ", "any-model"), "   ")

    def test_unreachable_server_returns_original(self):
        self.assertEqual(
            llm.polish("טקסט לבדיקה", "m", url=_DEAD, timeout=2), "טקסט לבדיקה")

    def test_list_models_unreachable_is_empty(self):
        self.assertEqual(llm.list_models(url=_DEAD, timeout=2), [])

    def test_available_unreachable_is_false(self):
        self.assertFalse(llm.available(url=_DEAD, timeout=2))


if __name__ == "__main__":
    unittest.main()
