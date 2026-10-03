"""faster-whisper wrapper — loads the model on demand and transcribes audio.

The model can be unloaded (`unload()`) to free GPU/CPU memory when the app is
idle or a fullscreen game is running, and is transparently reloaded on the next
transcription (`ensure_loaded()`)."""
import gc
import glob
import logging
import os
import sys
import threading

log = logging.getLogger("transcriber")
_dll_handles = []


def _add_cuda_dll_dirs():
    """Add the pip-installed NVIDIA CUDA DLL folders to the DLL search path.

    CTranslate2 (faster-whisper's GPU backend) needs cublas/cudnn DLLs that ship
    in the nvidia-* wheels under site-packages/nvidia/*/bin. Windows does not
    search those folders by default, so GPU inference fails with
    'cublas64_12.dll is not found'. This must run before importing faster_whisper.
    """
    roots = {os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")}
    try:
        import site
        for p in site.getsitepackages():
            roots.add(os.path.join(p, "nvidia"))
    except Exception:
        pass
    bindirs = []
    for nvidia_root in roots:
        bindirs.extend(glob.glob(os.path.join(nvidia_root, "*", "bin")))
    bindirs = sorted(set(bindirs))
    for bindir in bindirs:
        # add_dll_directory helps Python-loaded extensions; PATH is what
        # CTranslate2's own LoadLibrary calls actually honor on Windows.
        if hasattr(os, "add_dll_directory"):
            try:
                _dll_handles.append(os.add_dll_directory(bindir))
            except OSError:
                pass
        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    log.info("CUDA DLL dirs added: %s", bindirs)


_add_cuda_dll_dirs()

import numpy as np
from faster_whisper import WhisperModel


class Transcriber:
    """Holds a warm Whisper model in (GPU) memory and transcribes float32 audio."""

    def __init__(self, config):
        self.config = config
        self.language = config.get("language", "he")
        self.beam_size = config.get("beam_size", 5)
        # On CPU, beam search is expensive; greedy (beam 1) is 2-3x faster for a
        # small accuracy cost. Applied automatically whenever we run on the CPU.
        self.beam_size_cpu = config.get("beam_size_cpu", 1)
        self.cpu_threads = config.get("cpu_threads", 0)  # 0 = CTranslate2 auto-detect
        self.vad_filter = config.get("vad_filter", True)
        self.initial_prompt = config.get("initial_prompt") or None
        # Vocabulary uses one bounded channel; never duplicate the glossary in
        # initial_prompt and hotwords.
        self.glossary_prompt = config.get("glossary_prompt", True)
        self.device = None            # actual device in use after load
        self.fallback_reason = None   # set when GPU load failed and CPU took over
        self.model = None             # loaded lazily via load()/ensure_loaded()
        self._lock = threading.RLock()

    def load(self):
        """Load the model into memory (no-op if already loaded)."""
        with self._lock:
            if self.model is None:
                self.model = self._load_model(self.config)

    def ensure_loaded(self):
        """Load the model if it was released; safe to call before every use."""
        self.load()

    def is_loaded(self) -> bool:
        return self.model is not None

    def unload(self):
        """Release the model, freeing GPU/CPU memory (reloads on next use)."""
        with self._lock:
            if self.model is None:
                return
            self.model = None
            gc.collect()  # drops CTranslate2's GPU/CPU allocation
            log.info("Model unloaded — %s memory freed.", self.device or "device")

    def _load_model(self, config):
        model_name = config["model"]
        device = config.get("device", "cuda")
        compute_type = config.get("compute_type", "float16")
        if device == "cpu" and compute_type == "float16":
            compute_type = "int8"  # float16 is a GPU type; int8 is the CPU choice
        log.info("Loading model '%s' on %s (%s)...", model_name, device, compute_type)
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute_type,
                                 cpu_threads=self.cpu_threads)
            self.device = device
        except Exception as e:
            # Graceful fallback to CPU if CUDA is unavailable / misconfigured.
            # The caller can check device/fallback_reason to warn the user —
            # otherwise the only symptom is 10x slower transcription.
            log.warning("GPU load failed (%s). Falling back to CPU int8.", e)
            model = WhisperModel(model_name, device="cpu", compute_type="int8",
                                 cpu_threads=self.cpu_threads)
            self.device = "cpu"
            self.fallback_reason = str(e)
        log.info("Model loaded on %s (beam=%d).", self.device, self._effective_beam())
        return model

    def _effective_beam(self):
        """Fewer beams on CPU for speed; full beam on GPU for accuracy."""
        return self.beam_size_cpu if self.device == "cpu" else self.beam_size

    def _fit_hint(self, text, budget):
        """Keep complete words inside the actual model-token budget."""
        kept = []
        for word in (text or "").split():
            candidate = " ".join(kept + [word])
            if len(self.model.hf_tokenizer.encode(" " + candidate).ids) > budget:
                break
            kept.append(word)
        return " ".join(kept) or None

    def _prompt_inputs(self, hotwords, glossary):
        # faster-whisper adds hotwords AND initial_prompt to the decoder prefix.
        # Repeating a glossary in both overloaded that prefix and could cause
        # spoken English to disappear. Share one bounded vocabulary channel.
        budget = min(112, self.model.max_length // 2 - 8)
        prompt = self._fit_hint(self.initial_prompt, min(16, budget))
        used = len(self.model.hf_tokenizer.encode(" " + prompt).ids) if prompt else 0
        words, seen = [], set()
        sources = list(reversed(glossary or [])) if self.glossary_prompt else []
        sources.append(hotwords or "")
        for source in sources:
            for word in source.split():
                key = word.casefold()
                if key not in seen:
                    seen.add(key)
                    words.append(word)
        return prompt, self._fit_hint(" ".join(words), min(96, budget - used))

    def transcribe(self, audio: np.ndarray, hotwords: str = None,
                   glossary=None) -> str:
        """Transcribe a mono float32 numpy array (16 kHz) and return the text.

        hotwords: optional space-joined vocabulary (learned corrections / approved
        words / English glossary) that biases the model toward those words.
        glossary: optional English vocabulary, prioritized newest first when
        glossary_prompt is on. Vocabulary is deduplicated and bounded together
        with initial_prompt using the loaded model's actual tokenizer.
        """
        if audio is None or len(audio) == 0:
            return ""
        with self._lock:
            self.ensure_loaded()  # reload transparently if it was released
            prompt, vocabulary = self._prompt_inputs(hotwords, glossary)
            segments, _info = self.model.transcribe(
                audio,
                language=self.language,
                task="transcribe",
                beam_size=self._effective_beam(),
                vad_filter=self.vad_filter,
                initial_prompt=prompt,
                hotwords=vocabulary,
                condition_on_previous_text=False,
            )
            text = "".join(segment.text for segment in segments)
            return text.strip()
