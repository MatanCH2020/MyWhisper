"""Native Windows fullscreen/registration/GPU smoke check; no mic or user data.

Close the normal app first. A demo window enters/exits fullscreen twice. Only
the temporary Ctrl+Alt+Shift+F12 shortcut is registered. No synthetic key input.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock, patch

os.environ.pop("QT_QPA_PLATFORM", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
import main
from config import load_config
from hotkey import HotkeyManager
from transcriber import Transcriber
from fullscreen import foreground_is_fullscreen


def gpu_used():
    return int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used",
        "--format=csv,noheader,nounits"], text=True).splitlines()[0])


qapp = QApplication([])
app = main.Mywishper.__new__(main.Mywishper)
QObject.__init__(app)
app.config = dict(load_config(), release_on_fullscreen=True, idle_release_minutes=0)
app._closing = app._updating = app._loading = app._fullscreen_paused = False
app._state = main.State.IDLE
app._last_used = time.monotonic()
app._hotkey_ready = app._model_ever_ready = True
app.recorder, app.ui, app.tray = Mock(), Mock(), Mock()
app.recorder.recording = False
app.transcriber = Transcriber(app.config)
app.transcriber.load()
assert app.transcriber.device == "cuda"
warm_memory = gpu_used()
combo = "ctrl+alt+shift+f12"
app.hotkeys = HotkeyManager(combo, app.toggle)
app.hotkeys.start()
probe = HotkeyManager(combo, lambda: None)
window = QWidget()
window.setWindowTitle("MyWhisper — Fullscreen pause check")
layout = QVBoxLayout(window)
layout.addWidget(QLabel("MyWhisper fullscreen check — no microphone or personal data"))
button = QPushButton("התחל בדיקת מסך מלא")
layout.addWidget(button)
window.resize(640, 300)
failed = False
foreground_patch = None
if "--geometry-only" in sys.argv:
    # Background launchers cannot claim foreground rights. Only substitute the
    # foreground handle; geometry queries, native registration and CUDA are real.
    import fullscreen
    foreground_patch = patch.object(fullscreen._user32, "GetForegroundWindow", lambda: int(window.winId()))
    foreground_patch.start()
    print("Controlled foreground handle; real window geometry, hotkey registration and CUDA", flush=True)


def checked(function):
    def run():
        global failed
        try:
            function()
        except Exception:
            import traceback
            traceback.print_exc()
            failed = True
            qapp.exit(1)
    return run


def focus():
    window.raise_()
    window.activateWindow()
    import ctypes
    ctypes.windll.user32.SetForegroundWindow(int(window.winId()))


@checked
def begin():
    button.setEnabled(False)
    QTimer.singleShot(20000, lambda: qapp.exit(2))
    assert not foreground_is_fullscreen(), "Normal window classified as fullscreen"
    window.showFullScreen()
    focus()
    QTimer.singleShot(400, paused)


@checked
def paused():
    import ctypes
    from ctypes import wintypes
    from fullscreen import _user32, _MONITORINFO
    own = int(window.winId())
    fg = _user32.GetForegroundWindow()
    rect = wintypes.RECT()
    _user32.GetWindowRect(fg, ctypes.byref(rect))
    monitor = _MONITORINFO()
    monitor.cbSize = ctypes.sizeof(monitor)
    _user32.GetMonitorInfoW(_user32.MonitorFromWindow(fg, 2), ctypes.byref(monitor))
    print("Native geometry:", own == fg, tuple(getattr(rect, k) for k in ("left", "top", "right", "bottom")),
          tuple(getattr(monitor.rcMonitor, k) for k in ("left", "top", "right", "bottom")), flush=True)
    assert foreground_is_fullscreen(), "Native fullscreen not detected"
    app._resource_poll()
    assert app._fullscreen_paused and app.hotkeys._id is None
    assert not app.transcriber.is_loaded()
    probe.start()  # Windows must let another owner register the released combo.
    probe.stop()
    app.toggle()  # A queued hotkey callback must not reload or start recording.
    assert not app.transcriber.is_loaded()
    app.recorder.start.assert_not_called()
    print(f"Fullscreen: shortcut released; model unloaded; GPU used {warm_memory} -> {gpu_used()} MiB", flush=True)
    window.showNormal()
    focus()
    QTimer.singleShot(400, resumed)


@checked
def resumed():
    assert not foreground_is_fullscreen()
    app._resource_poll()
    assert app._hotkey_ready and app.hotkeys._id is not None
    assert not app.transcriber.is_loaded(), "Exit eagerly reloaded the model"
    try:
        probe.start()
    except OSError:
        pass
    else:
        raise AssertionError("Restored shortcut was not actually registered")
    print("Fullscreen exit: shortcut restored without model load", flush=True)
    window.showFullScreen()
    focus()
    QTimer.singleShot(400, conflict)


@checked
def conflict():
    assert foreground_is_fullscreen()
    app._resource_poll()
    probe.start()
    window.showNormal()
    focus()
    QTimer.singleShot(400, conflict_exit)


@checked
def conflict_exit():
    assert not foreground_is_fullscreen()
    app._resource_poll()
    app._resource_poll()
    assert not app._hotkey_ready and app.hotkeys._id is None
    assert app.tray.notify.call_count == 1
    assert not app.transcriber.is_loaded()
    print("Shortcut reclaimed by another owner: one warning, no crash/load", flush=True)
    qapp.exit(0)


window.show()
focus()
button.clicked.connect(begin)
if "--auto" in sys.argv:
    QTimer.singleShot(700, begin)
try:
    code = qapp.exec()
finally:
    if foreground_patch:
        foreground_patch.stop()
    probe.stop()
    app.hotkeys.stop()
    app.transcriber.unload()
    window.close()
sys.exit(code)
