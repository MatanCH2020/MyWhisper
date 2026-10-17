"""Compare local models on public FLEURS WAVs and synthetic English only.

--fixtures must contain hebrew-0.wav, hebrew-1.wav, english.wav and the matching
first two FLEURS test.tsv rows in references.tsv. No transcripts are printed.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import numpy as np
from config import DEFAULTS
from transcriber import Transcriber
from faster_whisper.audio import decode_audio


def words(text):
    return re.findall(r"[^\W_]+", text.casefold(), re.UNICODE)


def errors(reference, actual):
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(reference, 1):
        row = [i]
        for j, other in enumerate(actual, 1):
            row.append(min(row[-1] + 1, previous[j] + 1,
                           previous[j-1] + (word != other)))
        previous = row
    return previous[-1]


def gpu_used():
    result = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used",
                                      "--format=csv,noheader,nounits"], text=True)
    return int(result.splitlines()[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True, type=Path)
    args = parser.parse_args()
    hebrew = [decode_audio(str(args.fixtures / f"hebrew-{i}.wav")) for i in range(2)]
    english = decode_audio(str(args.fixtures / "english.wav"))
    references = [line.split("\t")[3] for line in
                  (args.fixtures / "references.tsv").read_text(encoding="utf-8").splitlines()]
    for model in ("ivrit-ai/whisper-large-v3-turbo-ct2", "ivrit-ai/whisper-large-v3-ct2"):
        before = gpu_used()
        tr = Transcriber(dict(DEFAULTS, model=model))
        start = time.monotonic()
        tr.load()
        load_ms = round((time.monotonic() - start) * 1000)
        try:
            if tr.device != "cuda":
                raise RuntimeError("GPU benchmark fell back to CPU")
            for i, audio in enumerate(hebrew):
                start = time.monotonic()
                text = tr.transcribe(audio)
                ref = words(references[i])
                print(json.dumps(dict(model=model, fixture=f"hebrew-{i}",
                    seconds=round(len(audio)/16000, 3), load_ms=load_ms,
                    elapsed_ms=round((time.monotonic()-start)*1000),
                    word_errors=errors(ref, words(text)), reference_words=len(ref),
                    gpu_used_delta_mib=gpu_used()-before)), flush=True)
            audio = np.concatenate((hebrew[0], np.zeros(4800, dtype=np.float32), english))
            start = time.monotonic()
            text = tr.transcribe(audio, hotwords="GitHub Python iOS Android",
                                 glossary=["GitHub", "Python", "iOS", "Android"])
            print(json.dumps(dict(model=model, fixture="mixed",
                seconds=round(len(audio)/16000, 3), elapsed_ms=round((time.monotonic()-start)*1000),
                hebrew_chars=len(re.findall("[א-ת]", text)),
                english_terms={term: bool(re.search(r"(?<![A-Za-z])"+term+r"(?![A-Za-z])", text, re.I))
                               for term in ("GitHub", "Python", "iOS", "Android")})), flush=True)
            tr.unload()
            tr.load()
            print(json.dumps(dict(model=model, reload_device=tr.device)), flush=True)
        finally:
            tr.unload()


if __name__ == "__main__":
    main()
