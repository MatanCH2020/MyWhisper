"""Local Apple Silicon Whisper backend. All model access is serialized."""
import gc
import importlib
import logging
import threading
import time
import numpy as np
from model_download import ModelDownload, MODEL_ID
from platform_support import data_dir

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, config):
        self.config = config
        self.model = self.device = self.fallback_reason = None
        self.last_diagnostics = {}
        self._lock = threading.RLock()
        self.download = ModelDownload(data_dir())
        self._vad = None

    def load(self):
        with self._lock:
            if self.model is not None:
                return
            if self.config.get("backend", "mlx") != "mlx" or self.config.get("model", MODEL_ID) != MODEL_ID:
                raise ValueError("This Mac beta supports the pinned ivrit.ai MLX model only")
            if not self.download.ready():
                raise RuntimeError("Download the model from Mac setup first")
            import mlx.core as mx
            if not mx.metal.is_available():
                raise RuntimeError("Metal is unavailable; no cloud fallback")
            self._module = importlib.import_module("mlx_whisper.transcribe")
            self.model = self._module.ModelHolder.get_model(str(self.download.path), mx.float16)
            from mlx_whisper.tokenizer import get_tokenizer
            self._tokenizer = get_tokenizer(self.model.is_multilingual,
                num_languages=self.model.num_languages, language=self.config.get("language", "he"), task="transcribe")
            self.device = "metal"

    ensure_loaded = load

    def is_loaded(self):
        return self.model is not None

    def unload(self):
        with self._lock:
            if self.model is None:
                return
            import mlx.core as mx
            mx.synchronize()
            self._module.ModelHolder.model = None
            self._module.ModelHolder.model_path = None
            self.model = None
            self._vad = None
            gc.collect()
            mx.clear_cache()

    def _prompt(self, hotwords, glossary):
        encode = self._tokenizer.encode
        def fit(words, budget):
            kept = []
            for word in words:
                if len(encode(" " + " ".join(kept + [word]))) > budget:
                    break
                kept.append(word)
            return " ".join(kept)
        prompt = fit((self.config.get("initial_prompt") or "").split(), 16)
        sources = list(reversed(glossary or [])) if self.config.get("glossary_prompt", True) else []
        sources.append(hotwords or "")
        words, seen = [], set()
        for source in sources:
            for word in source.split():
                if word.casefold() not in seen:
                    seen.add(word.casefold())
                    words.append(word)
        vocab = fit(words, 96)
        combined = " ".join(x for x in (prompt, vocab) if x)
        return fit(combined.split(), 112) or None

    def transcribe(self, audio, hotwords=None, glossary=None):
        self.last_diagnostics = {}
        if audio is None or len(audio) == 0:
            return ""
        audio = np.asarray(audio, dtype=np.float32)
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        peak = float(np.max(np.abs(audio)))
        self.last_diagnostics = {"duration": len(audio) / 16000, "rms": rms, "peak": peak,
                                 "silent": peak <= 0.0001 and rms <= 0.00001}
        if self.last_diagnostics["silent"]:
            return ""
        with self._lock:
            self.ensure_loaded()
            if self.config.get("vad_filter", True):
                from local_vad import LocalVAD
                if self._vad is None:
                    self._vad = LocalVAD()
                audio = self._vad.filter(audio)
            self.last_diagnostics["speech_seconds"] = len(audio) / 16000
            if not len(audio):
                return ""
            started = time.monotonic()
            result = self._module.transcribe(audio, path_or_hf_repo=str(self.download.path),
                language=self.config.get("language", "he"), task="transcribe",
                initial_prompt=self._prompt(hotwords, glossary), condition_on_previous_text=False,
                temperature=0.0, verbose=None, fp16=True)
            self.last_diagnostics["elapsed"] = time.monotonic() - started
            log.info("Local decode diagnostics: %s", self.last_diagnostics)
            return result["text"].strip()
