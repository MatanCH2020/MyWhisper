"""Native Windows HUD click/focus/paste regression with synthetic data only.

Close the regular app first. No microphone, user history or cloud credentials.
Uses native mouse input against its own test widgets to verify WM_MOUSEACTIVATE.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys

os.environ.pop("QT_QPA_PLATFORM", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit
from overlay import Overlay
from delivery import DestinationGuard, foreground_window
from paste import paste_text

app = QApplication([])
editor = QPlainTextEdit()
editor.setWindowTitle("MyWhisper — synthetic mode focus check")
editor.resize(640, 300)
editor.show()
editor.activateWindow()
editor.setFocus()
hud = Overlay(lambda: .4)
state = {"enabled": False, "connected": True, "eligible": True, "model": "demo",
         "models": [{"slug": "demo", "display_name": "Demo"}]}
hud.mode.update_status(state)
guard = None
selected = []
cursor = wintypes.POINT()
ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor))


def choose(cloud):
    selected.append(cloud)
    state['enabled'] = cloud
    hud.mode.update_status(state)


hud.mode.selected.connect(choose)


def click(button):
    point = button.mapToGlobal(button.rect().center())
    # Qt reports logical coordinates; the test process uses Qt's DPI context.
    scale = hud.devicePixelRatioF()
    # Native screen origin is needed on mixed-DPI desktops; map client point.
    native = wintypes.POINT(round((point.x()-hud.x())*scale), round((point.y()-hud.y())*scale))
    user = ctypes.windll.user32
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.ClientToScreen(int(hud.winId()), ctypes.byref(native))
    user.SetCursorPos(native.x, native.y)
    user.mouse_event(0x0002, 0, 0, 0, 0)
    user.mouse_event(0x0004, 0, 0, 0, 0)


def begin():
    global guard
    user = ctypes.windll.user32
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.SetForegroundWindow(int(editor.winId()))
    editor.setFocus()
    guard = DestinationGuard().start()
    hud.set_state('recording')
    QTimer.singleShot(500, lambda: click(hud.mode.cloud))
    QTimer.singleShot(800, check_cloud)


def check_cloud():
    if selected != [True] or not guard.unchanged():
        finish(False, 'cloud click stole focus or missed')
        return
    click(hud.mode.local)
    QTimer.singleShot(300, check_local)


def check_local():
    if selected != [True, False] or not guard.unchanged():
        finish(False, 'local click stole focus or missed')
        return
    hud.set_state('transcribing')
    # Native delivery implementation under test; use no private clipboard input.
    paste_text('Synthetic cloud mode sample 123', True, .1, destination_check=guard.unchanged)
    QTimer.singleShot(300, lambda: finish(editor.toPlainText() == 'Synthetic cloud mode sample 123', 'mode clicks and native paste'))


def finish(ok, label):
    if guard:
        guard.close()
    hud.close()
    ctypes.windll.user32.SetCursorPos(cursor.x, cursor.y)
    print(('PASS: ' if ok else 'FAIL: ') + label, flush=True)
    app.exit(0 if ok else 1)


QTimer.singleShot(800, begin)
QTimer.singleShot(12000, lambda: finish(False, 'deadline'))
sys.exit(app.exec())
