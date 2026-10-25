"""Fetch the small pinned Silero asset for builds, never at dictation time."""
import sys
from pathlib import Path
import threading
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from model_download import download_file

REVISION = "066409f3d9db45a30d21dfcbf9147826ffdd7052"
BASE = f"https://raw.githubusercontent.com/snakers4/silero-vad/{REVISION}"


def main():
    download_file(BASE + "/src/silero_vad/data/silero_vad.onnx",
                  ROOT / "app/assets/silero_vad.onnx", 2327524,
                  "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
                  threading.Event())
    with urllib.request.urlopen(BASE + "/LICENSE", timeout=20) as response:
        (ROOT / "app/assets/SILERO-LICENSE.txt").write_bytes(response.read())


if __name__ == "__main__":
    main()
