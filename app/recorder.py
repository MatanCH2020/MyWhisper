"""Microphone recording via sounddevice -> float32 numpy buffer at 16 kHz mono."""
import logging
import queue
import numpy as np
import sounddevice as sd

log = logging.getLogger("recorder")

SAMPLE_RATE = 16000  # Whisper expects 16 kHz
CHANNELS = 1


def has_input_device() -> bool:
    """True if Windows exposes a usable default input (microphone) device."""
    try:
        sd.query_devices(kind="input")  # raises if there is no default input
        return True
    except Exception:
        return False


# Input "devices" that are not real microphones — virtual mappers, loopback
# (records system audio, not your voice), line-in, etc. Filtered out of the UI.
_NON_MIC_KEYWORDS = ("sound mapper", "primary sound capture", "stereo mix",
                     "line in", "what u hear", "loopback", "wave out")


def _input_devices_raw():
    """[(index, name, hostapi), ...] for every device with input channels,
    plus the host-API index of the default input device (or None)."""
    devs = []
    pref_api = None
    try:
        try:
            pref_api = sd.query_devices(kind="input").get("hostapi")
        except Exception:
            pref_api = None
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                devs.append((i, (d.get("name") or "").strip(), d.get("hostapi")))
    except Exception:
        pass
    return devs, pref_api


def list_input_devices():
    """Real microphones only, de-duplicated by name.

    Drops virtual mappers, loopback (Stereo Mix), line-in, and cross-host-API
    duplicates (keeps only the default host API). Falls back to the unfiltered
    list if strict filtering would hide every device, so a machine with only an
    oddly-named mic is never left with an empty list.
    """
    devs, pref_api = _input_devices_raw()

    def build(strict):
        seen, out = set(), []
        for i, name, api in devs:
            if not name or name in seen:
                continue
            if strict:
                if any(k in name.lower() for k in _NON_MIC_KEYWORDS):
                    continue
                if pref_api is not None and api != pref_api:
                    continue
            seen.add(name)
            out.append((i, name))
        return out

    return build(True) or build(False)


def resolve_device(name):
    """Map an input-device name to a sounddevice index (checked against ALL
    input devices, not just the filtered display list); None if not present."""
    if not name:
        return None
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and (d.get("name") or "").strip() == name:
                return i
    except Exception:
        pass
    return None


def _rms_level(indata, prev):
    """Smoothed 0..1-ish loudness from an audio block (shared by mic monitor)."""
    rms = float(np.sqrt(np.mean(np.square(indata, dtype=np.float64))))
    target = min(1.0, rms * 12.0)
    return prev + (target - prev) * 0.5


class MicMonitor:
    """Lightweight input-level monitor for the settings mic test — opens a stream
    on a chosen device and exposes a live level, independent of the Recorder."""

    def __init__(self):
        self._stream = None
        self._level = 0.0

    def _callback(self, indata, frames, time_info, status):
        self._level = _rms_level(indata, self._level)

    def start(self, device_name=None):
        """Open the given mic (name or None=default). Raises if it can't open."""
        self.stop()
        self._level = 0.0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
            device=resolve_device(device_name), callback=self._callback)
        self._stream.start()

    def level(self):
        return self._level if self._stream is not None else 0.0

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._level = 0.0


class Recorder:
    """Streams microphone audio into a buffer between start() and stop()."""

    def __init__(self, device=None):
        self._stream = None
        self._chunks = []
        self._q = queue.Queue()
        self.recording = False
        self._level = 0.0  # smoothed RMS level (0..1-ish) for the live animation
        # Selected input device name (str), or None for the system default.
        self.device = device or None

    def set_device(self, device):
        """Set the input device by name (or None for the system default)."""
        self.device = device or None

    def _resolve_device(self):
        """Map the stored device name to a sounddevice index; None (system
        default) if unset or if the named device is no longer present."""
        idx = resolve_device(self.device)
        if self.device and idx is None:
            log.warning("Selected mic %r not found — using the system default.", self.device)
        return idx

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
            device=self._resolve_device(),
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
