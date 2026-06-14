"""Download the Kolbo-generated MP3 cues and convert them to local WAV assets.

Run once (already done during setup); re-run to regenerate. Uses av (PyAV),
which ships with faster-whisper, so no extra dependency is needed.
"""
import io
import sys
import urllib.request
import wave
from pathlib import Path

import av
import numpy as np

ASSETS = Path(__file__).resolve().parent / "assets"

SOUNDS = {
    "start": "https://kolboai-production.ams3.digitaloceanspaces.com/kolboai-media/text-to-sound/699b808a70320eee0c85ae30/69e004fe294d3c8642399364/A%20single%20tiny%20soft%20b....mp3",
    "stop": "https://kolboai-production.ams3.digitaloceanspaces.com/kolboai-media/text-to-sound/699b808a70320eee0c85ae30/69e004fe294d3c8642399364/A%20single%20tiny%20soft%20l....mp3",
}

RATE = 44100


def mp3_to_wav(url: str, out_path: Path):
    data = urllib.request.urlopen(url).read()
    container = av.open(io.BytesIO(data))
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    chunks = []
    for frame in container.decode(stream):
        for rframe in resampler.resample(frame):
            chunks.append(rframe.to_ndarray().reshape(-1))
    container.close()
    audio = np.concatenate(chunks).astype(np.int16)

    # Normalize peak to ~0.9 so per-app volume control starts from a clean level.
    peak = np.max(np.abs(audio.astype(np.float32))) or 1.0
    audio = (audio.astype(np.float32) * (0.9 * 32767 / peak)).astype(np.int16)

    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    print(f"Wrote {out_path} ({len(audio)} samples)")


def main():
    ASSETS.mkdir(exist_ok=True)
    for name, url in SOUNDS.items():
        mp3_to_wav(url, ASSETS / f"{name}.wav")
    print("Done.")


if __name__ == "__main__":
    main()
