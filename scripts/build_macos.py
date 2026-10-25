"""Build an ad-hoc signed app and DMG on macOS ARM64, without Apple credentials."""
import hashlib
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("Build requires native macOS ARM64")
    run(sys.executable, "scripts/fetch_mac_assets.py")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImageReader
    iconset = ROOT / "build/MyWhisper.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    reader = QImageReader(str(ROOT / "app/assets/icon.ico"))
    images = []
    for index in range(reader.imageCount()):
        reader.jumpToImage(index)
        images.append(reader.read())
    image = max(images, key=lambda img: img.width())
    if image.isNull():
        raise RuntimeError("Application icon cannot be read")
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            image.scaled(size * scale, size * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(
                str(iconset / f"icon_{size}x{size}{suffix}.png"))
    run("iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "build/MyWhisper.icns"))
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "scripts/macos.spec")
    app = ROOT / "dist/MyWhisper.app"
    run("codesign", "--verify", "--deep", "--strict", str(app))
    # The frozen probe imports native components without starting capture or UI.
    run(str(app / "Contents/MacOS/MyWhisper"), "--verify-bundle")
    staging = ROOT / "build/dmg-stage"
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / "MyWhisper.app"
    if target.exists():
        raise SystemExit("DMG staging already exists; use a fresh build workspace")
    shutil.copytree(app, target, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    shutil.copy2(ROOT / "docs/macos-beta.md", staging / "README-HE.md")
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "app/version.py").read_text()).group(1)
    dmg = ROOT / "dist" / f"MyWhisper-{version}-macos-arm64.dmg"
    run("hdiutil", "create", "-volname", "MyWhisper Beta", "-srcfolder", str(staging),
        "-ov", "-format", "UDZO", str(dmg))
    run("hdiutil", "verify", str(dmg))
    with dmg.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (ROOT / "dist/SHA256SUMS.txt").write_text(digest + "  " + dmg.name + "\n")
    frozen = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    (ROOT / "dist/macos-build-requirements.txt").write_text(frozen)


if __name__ == "__main__":
    main()
