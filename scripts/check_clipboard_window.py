"""Native Windows picker/hotkey check. Synthetic data; never touches the clipboard.

Uses a separate demo process as the covering window and Ctrl+Alt+Shift+F11/F12
as isolated test shortcuts. The installed MyWhisper app may remain running.
"""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QColor, QPixmap
from PySide6.QtWidgets import QApplication, QLabel


def pump(app, predicate, seconds=3):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.processEvents()
        if predicate():
            return True
        time.sleep(.01)
    return False


def main():
    app = QApplication([])
    if len(sys.argv) > 1 and sys.argv[1] == "--helper":
        from hotkey import HotkeyManager
        from platform_support import bring_forward
        window = QLabel("MyWhisper foreground test — synthetic covering window")
        window.setWindowTitle("MyWhisper synthetic covering window")
        window.resize(650, 300)
        window.show()
        manager = HotkeyManager('ctrl+alt+shift+f12', lambda: bring_forward(window))
        manager.start()
        app.processEvents()
        print(int(window.winId()), flush=True)
        return app.exec()

    import main as runtime
    from clipui import ClipPicker
    from hotkey import HotkeyManager
    from platform_support import _window_api
    import theme
    api = _window_api()
    key = ctypes.windll.user32.keybd_event
    key.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_size_t]
    key.restype = None
    app.setStyleSheet(theme.build_qss(theme.DARK))
    picker = ClipPicker(theme.DARK, on_pick=lambda entry: False)
    owner = SimpleNamespace(_clip_picker=picker)
    entries = [{'id': str(i), 'kind': 'text', 'text': f'demo {i} synthetic text'} for i in range(100)]
    runtime.clips.load = lambda: entries
    manager = HotkeyManager('ctrl+alt+shift+f11', lambda: runtime.Mywishper.show_clips(owner))
    helper = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--helper'],
                              stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    cursor = QCursor.pos()
    last_cursor = cursor
    try:
        hwnd = int(helper.stdout.readline().strip())
        manager.start()

        def press(function_key=0x7A):
            for vk in (0x11, 0x12, 0x10, function_key):
                key(vk, 0, 0, 0)
            for vk in (function_key, 0x10, 0x12, 0x11):
                key(vk, 0, 2, 0)
            pump(app, lambda: False, .15)

        def cover():
            press(0x7B)
            assert pump(app, lambda: api.GetForegroundWindow() == hwnd), 'Covering window did not get focus'

        cover()
        press()
        assert pump(app, picker.is_foreground), 'Hidden picker did not activate from native hotkey'
        picker.search.setText('demo')
        picker._apply_filter()
        picker.list.setCurrentRow(40)
        picker.list.scrollToItem(picker.list.item(40))
        scroll = picker.list.verticalScrollBar().value()
        cover()
        press()
        assert pump(app, picker.is_foreground), 'Covered picker did not activate'
        assert picker.search.text() == 'demo' and picker._current_id() == '40'
        assert picker.list.verticalScrollBar().value() == scroll
        press()
        assert pump(app, lambda: not picker.isVisible()), 'Active picker did not toggle closed'
        cover()
        press()
        assert pump(app, picker.is_foreground)
        picker.showMinimized()
        cover()
        press()
        assert pump(app, picker.is_foreground), 'Minimized picker did not restore'
        picker.showMaximized()
        app.processEvents()
        picker.showMinimized()
        cover()
        press()
        assert pump(app, picker.is_foreground)
        assert picker.isMaximized(), 'Maximized placement lost after minimization'
        picker.showNormal()
        assert pump(app, lambda: not picker.isMaximized()), 'Could not restore normal placement'
        for _ in range(5):
            cover()
            press()
            assert pump(app, picker.is_foreground)
            press()
            assert pump(app, lambda: not picker.isVisible())
        print('PASS native hotkey: hidden, covered, minimized, maximized, repeat toggles; state preserved')
        for screen in app.screens():
            last_cursor = screen.availableGeometry().center()
            QCursor.setPos(last_cursor)
            cover()
            press()
            assert pump(app, picker.is_foreground)
            assert pump(app, lambda: screen.availableGeometry().contains(picker.geometry())), (
                f'Picker outside monitor bounds: window={picker.geometry()} screen={screen.availableGeometry()} state={picker.windowState()}')
            press()
            assert pump(app, lambda: not picker.isVisible())
        print(f'PASS monitor placement: {len(app.screens())} connected screens')
        for screen in app.screens():
            last_cursor = screen.availableGeometry().center()
            QCursor.setPos(last_cursor)
            cover()
            press()
            assert pump(app, picker.is_foreground)
            picker.showMaximized()
            pump(app, lambda: False, .15)
            assert screen.availableGeometry().contains(picker.geometry())
            press()
            assert pump(app, lambda: not picker.isVisible())
        print('PASS maximized picker follows cursor between monitors')
        picker.showNormal()
        pump(app, lambda: False, .15)
        with tempfile.TemporaryDirectory() as folder:
            for width, height in ((1600, 900), (600, 1600), (32, 32)):
                picture = QPixmap(width, height)
                picture.fill(QColor('#3575e5'))
                path = Path(folder) / f'{width}x{height}.png'
                picture.save(str(path))
                picker.show_for([{'id': 'picture', 'kind': 'image', 'path': str(path)}])
                app.processEvents()
                before = picker.image_preview.viewport().size()
                picker._expand_btn.setChecked(True)
                app.processEvents()
                assert picker.image_preview.viewport().height() > before.height()
                picker.image_preview.actual_size()
                assert picker.image_preview._label.pixmap().width() == width
                picker._escape()
                picker._escape()
        print('PASS native image fit/expansion/100%: landscape, portrait, small')
    finally:
        manager.stop()
        picker.close()
        # Terminate only the synthetic helper process created by this check.
        helper.terminate()
        helper.wait(timeout=5)
        if QCursor.pos() == last_cursor:
            QCursor.setPos(cursor)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
