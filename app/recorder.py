"""Microphone recording via sounddevice -> float32 numpy buffer at 16 kHz mono."""
import logging
import queue
import numpy as np
import sounddevice as sd

log = logging.getLogger("recorder")

SAMPLE_RATE = 16000  # Whisper expects 16 kHz
CHANNELS = 1


class Recorder:
    """Streams microphone audio into a buffer between start() and stop()."""

    def __init__(self):
        self._stream = None
        self._chunks = []
        self._q = queue.Queue()
        self.recording = False
        self._level = 0.0  # smoothed RMS level (0..1-ish) for the live animation

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.warning("%s", status)
        # Track a smoothed loudness level for the recording animation.
        rms = float(np.sqrt(np.mean(np.square(indata, dtype=np.float64))))
        # Map RMS to a lively 0..1 range and smooth to avoid jitter.
        target = min(1.0, rms * 12.0)
        self._level += (target - self._level) * 0.5
        # Copy because sounddevice reuses the buffer.
        self._q.put(indata.copy())

    def get_level(self) -> float:
        """Current smoothed mic loudness in ~0..1, for the UI animation."""
        return self._level if self.recording else 0.0

    def start(self):
        if self.recording:
            return
        self._chunks = []
        self._q = queue.Queue()
        self._level = 0.0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        self.recording = True

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured mono float32 audio (may be empty)."""
        if not self.recording:
            return np.array([], dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        self.recording = False
        # Drain the queue into a single contiguous array.
        while not self._q.empty():
            self._chunks.append(self._q.get())
        if not self._chunks:
            return np.array([], dtype=np.float32)
        audio = np.concatenate(self._chunks, axis=0).flatten().astype(np.float32)
        return audio
