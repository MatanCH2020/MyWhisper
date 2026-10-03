"""The removed Ollama settings cannot change the local dictation path."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QObject
import config
import main


class DictationPipelineTest(unittest.TestCase):
    def test_old_ollama_config_is_ignored_and_learned_text_is_pasted(self):
        app = main.Mywishper.__new__(main.Mywishper)
        QObject.__init__(app)
        app.transcriber = Mock()
        app.transcriber.transcribe.return_value = "תמלול מקומי"
        app.config = {"llm_polish": True, "llm_compare": True, "llm_model": "obsolete",
                      "bidi_isolate": False, "restore_clipboard": False}
        app.ui, app.tray = Mock(), Mock()
        app.clipwatch = None
        with patch.object(main.corrections, "bias_terms", return_value=""), \
             patch.object(main.corrections, "english_terms", return_value=[]), \
             patch.object(main.corrections, "apply", return_value="טקסט מתוקן") as correct, \
             patch.object(main.history, "add") as history, \
             patch.object(main, "same_destination", return_value=True), \
             patch.object(main, "paste_text") as paste:
            app._worker([0])
        correct.assert_called_once_with("תמלול מקומי")
        history.assert_called_once_with("טקסט מתוקן")
        paste.assert_called_once_with("טקסט מתוקן", False, 0.5, destination_check=ANY)

    def test_retired_config_keys_are_removed_but_future_keys_survive(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(config, "CONFIG_PATH", Path(folder) / "config.json"):
            config.save_config({"llm_polish": True, "llm_model": "old", "future": 123})
            loaded = config.load_config()
        self.assertNotIn("llm_polish", loaded)
        self.assertNotIn("llm_model", loaded)
        self.assertEqual(loaded["future"], 123)
