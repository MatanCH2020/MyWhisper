"""Run the actual CPU installer and tests in a disposable directory, never user data."""
from pathlib import Path
import shutil
import os
import stat
import subprocess
import sys
import tempfile
import venv

source = Path(__file__).resolve().parent.parent
temp_root = Path(tempfile.gettempdir()).resolve()
target = Path(tempfile.mkdtemp(prefix="mywhisper-clean-", dir=temp_root)).resolve()
print(f"Isolated installation: {target}", flush=True)
try:
    for folder in ("app", "scripts", "tests"):
        shutil.copytree(source / folder, target / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "split_ui.py"))
    for file in ("requirements.txt", "requirements-cuda.txt", "CHANGELOG.md"):
        shutil.copy2(source / file, target / file)
    venv.EnvBuilder(with_pip=True).create(target / ".venv")
    python = target / ".venv/Scripts/python.exe"
    for command in (
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(target / "scripts/setup.ps1"), "-SkipCuda"],
        [str(python), "-m", "unittest", "discover", "tests"],
    ):
        result = subprocess.run(command, cwd=target, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=1200)
        if result.returncode:
            print(result.stdout[-6000:])
            print(result.stderr[-6000:])
            raise SystemExit(result.returncode)
        print(result.stdout[-500:] + result.stderr[-600:], flush=True)
    print("Clean installation and regression tests passed.")
finally:
    # Resolve and verify the exact generated target before recursive cleanup.
    if target.parent != temp_root or not target.name.startswith("mywhisper-clean-"):
        raise RuntimeError("Refusing cleanup outside the isolated test directory")
    def writable_retry(function, path, exc):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        function(path)
    shutil.rmtree(target, onexc=writable_retry)
