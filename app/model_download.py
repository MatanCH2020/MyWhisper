"""Explicit, resumable, integrity-checked model download. No inference/network coupling."""
import hashlib
import json
from pathlib import Path
import threading
import urllib.request

MODEL_ID = "mlx-community/ivrit-ai-whisper-large-v3-mlx"
REVISION = "097c0cb2bb4288a3c82f6d524a39c4ce09afa187"
FILES = {
    "config.json": (269, "34982ce6ae286095000f82ae9583b3431639e8b092bf60c961f203745e6500e3"),
    "weights.safetensors": (3083275629, "c7b9d7b9df7cb669101a3b166f6b37bcfb3dbe1f20b74e8b6e2867019472ab93"),
}


class DownloadCancelled(Exception):
    pass


def checksum(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel and cancel.is_set():
                raise DownloadCancelled()
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url, destination, size, sha256, cancel, progress=lambda done, total: None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size == size and checksum(destination, cancel) == sha256:
        progress(size, size)
        return
    partial = destination.with_name(destination.name + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        partial.unlink()
        offset = 0
    if cancel.is_set():
        raise DownloadCancelled()
    if offset < size:
        headers = {"User-Agent": "MyWhisper-model-download", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status == 206:
                if response.headers.get("Content-Range", "") != f"bytes {offset}-{size - 1}/{size}":
                    raise ValueError("Unexpected partial response")
            elif response.status == 200:
                offset = 0  # server did not honor resume; safely overwrite partial
            else:
                raise ValueError("Unexpected download response")
            with partial.open("ab" if offset else "wb") as stream:
                progress(offset, size)
                while True:
                    if cancel.is_set():
                        raise DownloadCancelled()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if offset + len(chunk) > size:
                        raise ValueError("Download larger than pinned artifact")
                    stream.write(chunk)
                    offset += len(chunk)
                    progress(offset, size)
    if offset != size:
        raise IOError("Incomplete download; retry resumes the partial file")
    if checksum(partial, cancel) != sha256:
        partial.unlink(missing_ok=True)
        raise ValueError("Model checksum mismatch; download discarded")
    partial.replace(destination)


class ModelDownload:
    def __init__(self, root):
        self.path = Path(root) / "models" / REVISION
        self.cancelled = threading.Event()
        self._lock = threading.Lock()

    def ready(self):
        try:
            if any((self.path / name).stat().st_size != size for name, (size, _) in FILES.items()):
                return False
            receipt = json.loads((self.path / "verified.json").read_text())
            return receipt == {name: [size, digest, (self.path / name).stat().st_mtime_ns]
                               for name, (size, digest) in FILES.items()}
        except (OSError, ValueError):
            return False

    def download(self, progress=lambda done, total: None):
        with self._lock:
            total = sum(size for size, _ in FILES.values())
            done = 0
            for name, (size, digest) in FILES.items():
                download_file(f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}",
                              self.path / name, size, digest, self.cancelled,
                              lambda current, subtotal, base=done: progress(base + current, total))
                done += size
            receipt = {name: [size, digest, (self.path / name).stat().st_mtime_ns]
                       for name, (size, digest) in FILES.items()}
            temporary = self.path / "verified.tmp"
            temporary.write_text(json.dumps(receipt))
            temporary.replace(self.path / "verified.json")
