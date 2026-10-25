"""Native Windows screenshot capture check with temporary storage and no user content.

Close MyWhisper before running. Preserves supported clipboard formats, restoring
only if the clipboard has not changed since this check. Creates its own demo UI.
"""
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.pop("QT_QPA_PLATFORM", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication, QLabel
from clipwatch import ClipboardWatcher
from clipui import ClipPicker
import clips
import theme
import winclipboard
from updater import _ensure_stopped


def pump(app, predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        app.processEvents()
        if predicate():
            return True
        time.sleep(.02)
    return False


def main():
    _ensure_stopped()
    app = QApplication([])
    app.setStyleSheet(theme.build_qss(theme.LIGHT))
    demo = QLabel("MyWhisper screenshot check — synthetic content only")
    demo.resize(620, 180)
    demo.show()
    pump(app, lambda: demo.isVisible())
    image = demo.grab().toImage()
    assert not image.isNull()
    watcher = picker = None
    sequence = 0
    with tempfile.TemporaryDirectory() as folder, winclipboard.WindowsClipboard() as native:
        clips.CLIPS_PATH = Path(folder) / 'clips.json'
        clips.IMAGE_DIR = Path(folder) / 'images'
        with native.opened():
            previous = native._snapshot()
        try:
            watcher = ClipboardWatcher()
            md = QMimeData()
            md.setImageData(image)
            md.setData('application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"', b'\x01\0\0\0')
            md.setData('application/x-qt-windows-mime;value="CanUploadToCloudClipboard"', b'\0\0\0\0')
            app.clipboard().setMimeData(md)
            sequence = winclipboard._sequence()
            assert pump(app, lambda: len(clips.load()) == 1), 'Native clipboard event did not persist image'
            entry = clips.load()[0]
            assert entry['kind'] == 'image' and Path(entry['path']).is_file()
            print('Native screenshot + allow-history flag: PASS', flush=True)
            picker = ClipPicker(theme.LIGHT, lambda e: True)
            picker.show_for([entry])
            picker._set_filter('image')
            app.processEvents()
            assert not picker.image_preview._pixmap.isNull()
            print('Persisted image in gallery and full preview: PASS', flush=True)
            # Real system clipboard receives an explicit prohibition next.
            denied = QMimeData()
            different = image.copy()
            different.fill(0xff0099ff)
            denied.setImageData(different)
            denied.setData('application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"', b'\0\0\0\0')
            app.clipboard().setMimeData(denied)
            sequence = winclipboard._sequence()
            pump(app, lambda: False, .3)
            watcher.close()
            assert len(clips.load()) == 1
            print('Explicit history exclusion stays blocked: PASS', flush=True)
        finally:
            if watcher:
                watcher.close()
            if picker:
                picker.close()
            demo.close()
            if sequence:
                print('Previous clipboard restored:', native.restore(previous, sequence), flush=True)


if __name__ == '__main__':
    main()
