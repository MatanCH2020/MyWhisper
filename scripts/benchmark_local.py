"""Compare local backends on the same public/synthetic audio; reports contain no text.

manifest: [{"id":"he-01", "kind":"public", "audio":"sample.wav", "reference":"..."}]
The model must already be installed. This does not record, paste, or write history.
"""
import argparse
import json
from pathlib import Path
import platform
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import av
import numpy as np
from config import DEFAULTS
from platform_support import IS_MAC
from version import __version__


def word_errors(reference, hypothesis):
    tokenize = lambda s: re.findall(r"[\w]+", s.casefold())
    expected, actual = tokenize(reference), tokenize(hypothesis)
    previous = list(range(len(actual) + 1))
    for i, token in enumerate(expected, 1):
        row = [i]
        for j, other in enumerate(actual, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (token != other)))
        previous = row
    return previous[-1], len(expected)


def read_audio(path):
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
    chunks = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                chunks.append(converted.to_ndarray().ravel())
        for converted in resampler.resample(None):
            chunks.append(converted.to_ndarray().ravel())
    return np.concatenate(chunks).astype(np.float32) if chunks else np.array([], dtype=np.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not cases or any(c.get("kind") not in ("public", "synthetic") for c in cases):
        raise SystemExit("Only an explicitly public/synthetic corpus is accepted")
    if IS_MAC:
        from mlx_transcriber import Transcriber
    else:
        from transcriber import Transcriber
    engine = Transcriber(dict(DEFAULTS))
    started = time.monotonic()
    engine.load()
    load_seconds = time.monotonic() - started
    results = []
    try:
        for case in cases:
            audio = read_audio(args.manifest.parent / case["audio"])
            started = time.monotonic()
            text = engine.transcribe(audio)
            elapsed = time.monotonic() - started
            errors, words = word_errors(case["reference"], text)
            results.append({"id": case["id"], "seconds": len(audio) / 16000,
                            "elapsed": elapsed, "word_errors": errors, "reference_words": words,
                            "diagnostics": engine.last_diagnostics})
        report = {"version": __version__, "system": platform.system(), "os_version": platform.mac_ver()[0],
                  "architecture": platform.machine(), "backend": DEFAULTS["backend"],
                  "device": engine.device, "load_seconds": load_seconds, "cases": results}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Completed {len(results)} cases; report has numeric metrics, not transcription text")
    finally:
        engine.unload()


if __name__ == "__main__":
    main()
