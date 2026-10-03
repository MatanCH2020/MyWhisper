"""Local GPU check using public Hebrew + synthetic English, never microphone/cloud.

Pass a fixture directory containing hebrew-0.wav, hebrew-1.wav, english.wav.
--compare-legacy replays the 1.13.1 decoder settings for the same audio.
Only counts and expected synthetic English terms are printed, never transcripts.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import corrections
from config import load_config
from transcriber import Transcriber
from faster_whisper.audio import decode_audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--compare-legacy", action="store_true")
    args = parser.parse_args()
    root = args.fixtures
    he = decode_audio(str(root / "hebrew-0.wav"))
    en = decode_audio(str(root / "english.wav"))
    pause = np.zeros(4800, dtype=np.float32)
    fixtures = {"middle": np.concatenate((he[:64000], pause, en, pause, he[64000:128000])),
                "start": np.concatenate((en, pause, he[:96000])),
                "end": np.concatenate((he[:96000], pause, en)),
                "hebrew_control": decode_audio(str(root / "hebrew-1.wav"))}
    cfg = load_config()
    tr = Transcriber(cfg)
    tr.load()
    terms = corrections.english_terms()
    eng = list(dict.fromkeys(terms))[:100]
    corr = [v for v in dict.fromkeys(corrections.list_corrections().values()) if v not in eng][:100-len(eng)]
    approved = [w for w in corrections._load_dictionary() if w not in eng + corr]
    remaining = 100 - len(eng) - len(corr)
    legacy_bias = " ".join(eng + corr + (approved[-remaining:] if remaining else []))
    legacy_prompt = tr.initial_prompt or ""
    if tr.glossary_prompt and terms:
        legacy_prompt = (legacy_prompt + " מונחים באנגלית: " + ", ".join(terms[:30]) + ".").strip()
    results = []
    try:
        for variant in (["before", "after"] if args.compare_legacy else ["after"]):
            for name, audio in fixtures.items():
                start = time.monotonic()
                if variant == "before":
                    segments, _ = tr.model.transcribe(audio, language=tr.language,
                        beam_size=tr._effective_beam(), vad_filter=tr.vad_filter,
                        initial_prompt=legacy_prompt or None, hotwords=legacy_bias or None)
                    text = "".join(s.text for s in segments).strip()
                else:
                    text = tr.transcribe(audio, hotwords=corrections.bias_terms(),
                                        glossary=terms + ["iOS", "Android"])
                result = {"variant": variant, "fixture": name, "device": tr.device,
                    "elapsed_ms": round((time.monotonic()-start)*1000),
                    "english_terms": {key: bool(re.search(r"(?<![A-Za-z])"+key+r"(?![A-Za-z])", text, re.I))
                                      for key in ("iOS", "Android", "GitHub", "Python")},
                    "hebrew_chars": len(re.findall("[א-ת]", text)), "chars": len(text)}
                results.append(result)
                print(json.dumps(result), flush=True)
    finally:
        tr.unload()
    if not all(r["english_terms"]["iOS"] and r["english_terms"]["Android"]
               for r in results if r["variant"] == "after" and r["fixture"] in ("middle", "end")):
        raise SystemExit("Mixed dictation platform-word check failed")
    if not any(r["hebrew_chars"] > 0 for r in results if r["variant"] == "after" and r["fixture"] == "hebrew_control"):
        raise SystemExit("Hebrew control check failed")


if __name__ == "__main__":
    main()
