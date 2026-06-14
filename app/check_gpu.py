"""Sanity check: record 4 seconds from the mic and transcribe — verifies GPU + Hebrew.

Run after setup:  .venv\\Scripts\\python app\\check_gpu.py
Speak a Hebrew sentence (with a question, e.g. "מה השעה עכשיו?") during the countdown.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_config
from recorder import Recorder
from transcriber import Transcriber


def main():
    cfg = load_config()
    print("Loading model (first run downloads it — may take a few minutes)...")
    transcriber = Transcriber(cfg)

    rec = Recorder()
    print("\nRecording 4 seconds — speak Hebrew now!")
    rec.start()
    for i in range(4, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    audio = rec.stop()
    print(f"Captured {len(audio)} samples. Transcribing...")

    text = transcriber.transcribe(audio)
    print("\n=== RESULT ===")
    print(text or "(empty)")


if __name__ == "__main__":
    main()
