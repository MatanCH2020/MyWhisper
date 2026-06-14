"""Soft audio feedback cues. Plays the Kolbo-generated WAV chimes via sounddevice
at a gentle, configurable volume. Falls back to a quiet winsound beep if the WAV
assets are missing."""
import wave
import winsound
from pathlib import Path

import numpy as np
import sounddevice as sd

ASSETS = Path(__file__).resolve().parent / "assets"

_enabled = True
_volume = 0.35  # gentle default (0..1)
_cache = {}


def configure(enabled: bool = True, volume: float = 0.35):
    global _enabled, _volume
    _enabled = enabled
    _volume = max(0.0, min(1.0, float(volume)))


def _load(name):
    if name in _cache:
        return _cache[name]
    path = ASSETS / f"{name}.wav"
    data = None
    if path.exists():
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                frames = w.readframes(w.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            data = (audio, rate)
        except Exception:
            data = None
    _cache[name] = data
    return data


def _play(name, fallback_freq):
    if not _enabled:
        return
    clip = _load(name)
    if clip is None:
        try:
            winsound.Beep(fallback_freq, 100)
        except RuntimeError:
            pass
        return
    audio, rate = clip
    try:
        sd.play(audio * _volume, rate)
    except Exception:
        pass


def play(name):
    """Public test-play of a named cue ('start' or 'stop'), ignoring _enabled."""
    clip = _load(name)
    if clip is None:
        return
    audio, rate = clip
    try:
        sd.play(audio * _volume, rate)
    except Exception:
        pass


def import_sound(name: str, src_path: str) -> bool:
    """Convert any audio file (wav/mp3/m4a/...) into assets/<name>.wav.

    Returns True on success. Reloads the cache so the new sound is used at once.
    """
    import av  # ships with faster-whisper

    dst = ASSETS / f"{name}.wav"
    try:
        ASSETS.mkdir(exist_ok=True)
        container = av.open(str(src_path))
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=44100)
        chunks = []
        for frame in container.decode(stream):
            for rframe in resampler.resample(frame):
                chunks.append(rframe.to_ndarray().reshape(-1))
        container.close()
        audio = np.concatenate(chunks).astype(np.float32)
        peak = float(np.max(np.abs(audio))) or 1.0
        audio = (audio * (0.9 * 32767 / peak)).astype(np.int16)
        with wave.open(str(dst), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(audio.tobytes())
        _cache.pop(name, None)  # force reload on next play
        return True
    except Exception as e:
        print(f"[sounds] import_sound failed: {e}")
        return False


def start_recording():
    _play("start", 660)


def stop_recording():
    _play("stop", 440)


def done():
    # No extra sound; the paste itself is the confirmation.
    pass


def error():
    _play("stop", 220)
