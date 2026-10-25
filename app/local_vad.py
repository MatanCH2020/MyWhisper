"""Silero VAD ONNX inference at 16 kHz; no torch, audio files, or network."""
from pathlib import Path
import numpy as np


def speech_spans(probabilities, sample_count, threshold=0.5):
    """Hysteresis with 160ms padding, 480ms silence, and 96ms minimum speech."""
    spans, start, silence = [], None, None
    for index, probability in enumerate(probabilities):
        position = index * 512
        if probability >= threshold:
            if start is None:
                start = position
            silence = None
        elif probability < threshold - 0.15 and start is not None:
            if silence is None:
                silence = position
            if position - silence >= 7680:
                if silence - start >= 1536:
                    spans.append((max(0, start - 2560), min(sample_count, silence + 2560)))
                start = silence = None
    if start is not None and sample_count - start >= 1536:
        spans.append((max(0, start - 2560), sample_count))
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


class LocalVAD:
    def __init__(self):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(Path(__file__).parent / "assets/silero_vad.onnx"),
                                            sess_options=options, providers=["CPUExecutionProvider"])

    def filter(self, audio):
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        probabilities = []
        for offset in range(0, len(audio), 512):
            chunk = np.zeros((1, 512), dtype=np.float32)
            values = audio[offset:offset + 512]
            chunk[0, :len(values)] = values
            frame = np.concatenate((context, chunk), axis=1)
            probability, state = self.session.run(None, {
                "input": frame, "state": state, "sr": np.array(16000, dtype=np.int64)})
            probabilities.append(float(probability.item()))
            context = frame[:, -64:]
        spans = speech_spans(probabilities, len(audio))
        return np.concatenate([audio[a:b] for a, b in spans]) if spans else np.array([], dtype=np.float32)
