"""Release-tag updater. Uses only stdlib, so a broken venv cannot break rollback."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

RELEASE_URL = "https://api.github.com/repos/MatanCH2020/MyWhisper/releases/latest"
TAG_RE = re.compile(r"v?(\d+\.\d+\.\d+)")


def run(root, *args, timeout=120):
    result = subprocess.run(args, cwd=root, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-2000:]}")
    return result.stdout.strip()


def _git(root, *args):
    return run(root, "git", "-C", str(root), *args)


def _child(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Update path escapes the installation directory")
    return path


def _clean(root):
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise RuntimeError("Not the installation repository root")
    if _git(root, "status", "--porcelain", "--untracked-files=all", "--", ".",
            ":(exclude).update-backups", ":(exclude).mywhisper-update.lock"):
        raise RuntimeError("Local changes found. Save/commit them before updating; nothing was overwritten.")


def prepare(root, tag=None):
    """Validate while the app remains running. Return an exact verified tag."""
    root = Path(root).resolve()
    _clean(root)
    if tag is None:
        req = urllib.request.Request(RELEASE_URL, headers={"User-Agent": "MyWhisper"})
        with urllib.request.urlopen(req, timeout=15) as response:
            release = json.load(response)
        if release.get("draft") or release.get("prerelease"):
            raise RuntimeError("The release is not a stable published version")
        tag = release.get("tag_name", "")
    match = TAG_RE.fullmatch(tag)
    if not match:
        raise ValueError("Invalid release tag")
    _git(root, "fetch", "--no-tags", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    version_source = _git(root, "show", f"{tag}:app/version.py")
    if not re.search(r'__version__\s*=\s*[\'\"]' + re.escape(match[1]) + r'[\'\"]', version_source):
        raise RuntimeError("Release tag and application version disagree")
    # Validate the target's smoke check and backup capacity before shutdown.
    _git(root, "cat-file", "-e", f"{tag}:scripts/verify_install.py")
    env = _child(root, root / ".venv")
    required = sum(p.stat().st_size for p in env.rglob("*") if p.is_file())
    if shutil.disk_usage(root).free < required * 2 + 2 * 1024**3:
        raise RuntimeError("Not enough free disk space for an environment backup and rollback")
    return tag


def _backup(root):
    parent = _child(root, root / ".update-backups")
    backup = _child(root, parent / uuid.uuid4().hex)
    backup.mkdir(parents=True)
    old_ref = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    (backup / "state.json").write_text(json.dumps({"commit": old_ref, "branch": branch}), encoding="utf-8")
    _git(root, "archive", "--format=zip", "-o", str(backup / "code.zip"), "HEAD")
    env = _child(root, root / ".venv")
    if env.exists():
        shutil.copytree(env, _child(root, backup / ".venv"))
    return backup, old_ref, branch


def _install(root):
    run(root, "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(root / "scripts" / "setup.ps1"), timeout=3600)
    python = root / ".venv" / "Scripts" / "python.exe"
    run(root, str(python), "-m", "pip", "check")
    run(root, str(python), str(root / "scripts" / "verify_install.py"))


def _restore(root, backup, old_ref, branch):
    # Checkout refuses conflicting user changes; there is deliberately no force/reset.
    _git(root, "checkout", branch if branch != "HEAD" else old_ref)
    env, saved = _child(root, root / ".venv"), _child(root, backup / ".venv")
    if saved.exists():
        if env.exists():
            env.rename(_child(root, backup / "failed-environment"))
        shutil.copytree(saved, env)


def apply_update(root, tag):
    """Called only after shutdown. Keep backups on disk for manual recovery too."""
    if not TAG_RE.fullmatch(tag):
        raise ValueError("Invalid release tag")
    root = Path(root).resolve()
    _clean(root)
    backup, old_ref, branch = _backup(root)
    try:
        _git(root, "checkout", "--detach", tag)
        _install(root)
    except Exception as original:
        try:
            _restore(root, backup, old_ref, branch)
        except Exception as recovery:
            raise RuntimeError(f"Update and rollback failed. Backup: {backup}. {recovery}") from original
        raise RuntimeError(f"Update failed; previous version restored. Backup: {backup}. {original}") from original
    return backup


def _wait_for_exit(pid):
    import ctypes
    from ctypes import wintypes
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenProcess.restype = wintypes.HANDLE
    dll.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = dll.OpenProcess(0x100000, False, pid)
    if handle:
        try:
            if dll.WaitForSingleObject(handle, 30000) != 0:
                raise RuntimeError("Application did not exit; update cancelled")
        finally:
            dll.CloseHandle(handle)
    elif ctypes.get_last_error() not in (0, 87):
        raise RuntimeError("Could not confirm that the application has exited")


def _ensure_stopped():
    import ctypes
    from ctypes import wintypes
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    dll.OpenMutexW.restype = wintypes.HANDLE
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = dll.OpenMutexW(0x100000, False, "MyWhisper_MatanDigital_SingleInstance_v1")
    if handle:
        dll.CloseHandle(handle)
        raise RuntimeError("Close MyWhisper before updating, or use Settings > Update in the app.")
    if ctypes.get_last_error() not in (0, 2):
        raise RuntimeError("Could not check the running application; update cancelled")


@contextmanager
def _app_guard():
    """Reserve the same mutex as the app, preventing launches during replacement."""
    import ctypes
    from ctypes import wintypes
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    dll.CreateMutexW.restype = wintypes.HANDLE
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = dll.CreateMutexW(None, False, "MyWhisper_MatanDigital_SingleInstance_v1")
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        if error == 183:
            raise RuntimeError("MyWhisper started again; update cancelled")
        yield
    finally:
        dll.CloseHandle(handle)


@contextmanager
def _update_lock(root):
    import msvcrt
    with (root / ".mywhisper-update.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    if args.parent_pid:
        _wait_for_exit(args.parent_pid)
    _ensure_stopped()
    restart, code = False, 0
    with _app_guard(), _update_lock(root):
        try:
            tag = prepare(root, args.tag)
            backup = apply_update(root, tag)
            print(f"Update verified. Previous installation backed up at {backup}")
            restart = True
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            code = 1
            # A failed rollback needs manual attention. Relaunch only if coherent.
            try:
                run(root, str(root / ".venv/Scripts/python.exe"), str(root / "scripts/verify_install.py"))
                restart = True
            except Exception:
                pass
    # Release both locks before the silent launcher attempts to acquire the mutex.
    if restart:
        subprocess.Popen(["wscript.exe", str(root / "scripts/run_mywishper.vbs")], cwd=root)
    return code


if __name__ == "__main__":
    sys.exit(main())
