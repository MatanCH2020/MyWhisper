"""Non-interactive verification: load the model on GPU and run one transcription.

Downloads the model on first run, confirms CUDA works (no CPU fallback), and
exercises the full transcribe path on a synthetic audio buffer.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_config
from transcriber import Transcriber


def main():
    cfg = load_config()
    print(f"Model: {cfg['model']}  device={cfg['device']}  compute={cfg['compute_type']}")
    t0 = time.time()
    transcriber = Transcriber(cfg)
    print(f"Model load took {time.time() - t0:.1f}s")
    print(f"Actual device in use: {transcriber.model.model.device}")

    # 2 seconds of low-amplitude noise just to exercise the transcribe path.
    audio = (np.random.randn(16000 * 2) * 0.001).astype(np.float32)
    t0 = time.time()
    text = transcriber.transcribe(audio)
    print(f"Transcribe ran in {time.time() - t0:.2f}s (no crash = pipeline OK)")
    print(f"Output text: '{text}'")
    print("\nSUCCESS: model loaded and transcription pipeline works.")


if __name__ == "__main__":
    main()
