"""faster-whisper wrapper — loads the model once and transcribes audio buffers."""
import glob
import logging
import os
import sys

log = logging.getLogger("transcriber")


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
                os.add_dll_directory(bindir)
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
        self.vad_filter = config.get("vad_filter", True)
        self.initial_prompt = config.get("initial_prompt") or None
        self.device = None            # actual device in use after load
        self.fallback_reason = None   # set when GPU load failed and CPU took over
        self.model = self._load_model(config)

    def _load_model(self, config):
        model_name = config["model"]
        device = config.get("device", "cuda")
        compute_type = config.get("compute_type", "float16")
        if device == "cpu" and compute_type == "float16":
            compute_type = "int8"  # float16 is a GPU type; int8 is the CPU choice
        log.info("Loading model '%s' on %s (%s)...", model_name, device, compute_type)
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
            self.device = device
        except Exception as e:
            # Graceful fallback to CPU if CUDA is unavailable / misconfigured.
            # The caller can check device/fallback_reason to warn the user —
            # otherwise the only symptom is 10x slower transcription.
            log.warning("GPU load failed (%s). Falling back to CPU int8.", e)
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            self.device = "cpu"
            self.fallback_reason = str(e)
        log.info("Model loaded on %s.", self.device)
        return model

    def transcribe(self, audio: np.ndarray, hotwords: str = None) -> str:
        """Transcribe a mono float32 numpy array (16 kHz) and return the text.

        hotwords: optional space-joined vocabulary (learned corrections / approved
        words) that biases the model toward producing those words. faster-whisper
        folds it into the prompt alongside initial_prompt.
        """
        if audio is None or len(audio) == 0:
            return ""
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            initial_prompt=self.initial_prompt,
            hotwords=hotwords or None,
        )
        text = "".join(segment.text for segment in segments)
        return text.strip()
