"""Force real GPU inference to confirm cublas/cudnn DLLs load (no VAD shortcut)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_config
from transcriber import Transcriber


def main():
    cfg = load_config()
    t = Transcriber(cfg)
    # 3s tone sweep, loud enough that VAD won't skip it -> forces the GPU encoder.
    sr = 16000
    tt = np.linspace(0, 3, sr * 3, dtype=np.float32)
    audio = (0.3 * np.sin(2 * np.pi * (200 + 100 * tt) * tt)).astype(np.float32)
    # Bypass VAD so the encoder definitely runs on the GPU.
    t.vad_filter = False
    text = t.transcribe(audio)
    print(f"GPU inference ran without DLL errors. Output: '{text}'")
    print("SUCCESS: cublas/cudnn loaded and GPU inference works.")


if __name__ == "__main__":
    main()
