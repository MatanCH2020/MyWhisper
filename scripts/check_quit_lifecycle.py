"""Synthetic Qt shutdown check. No model, microphone, clipboard or user data.

Run with --native for a real Windows window; default is offscreen. The check
must exit on explicit quit, even after X-to-tray and while an update message
is open. A watchdog exits with failure if any close event blocks shutdown.
"""
import os
from pathlib import Path
import sys
import threading

if '--native' not in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
else:
    os.environ.pop('QT_QPA_PLATFORM', None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'app'))

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from ui import AppUI, MainWindow
from config import DEFAULTS
import theme


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    ctl = AppUI(dict(DEFAULTS), lambda: 0, lambda c: None,
                get_history=lambda: [], clear_history=lambda: None,
                test_sound=lambda name: None, import_sound=lambda *args: False,
                list_corrections=lambda: {}, english_terms=lambda: [])
    window = ctl._win = MainWindow(ctl, theme.DARK)
    window.setWindowTitle('MyWhisper synthetic shutdown check')
    window.show()
    result = {'closed_to_tray': False, 'quit': False, 'modal_returned': False}
    app.aboutToQuit.connect(lambda: result.update(quit=True))
    def exercise():
        window.close()
        result['closed_to_tray'] = not window.isVisible() and not window._force_close
        if '--hidden' not in sys.argv:
            window.show()
        def request():
            if '--worker' in sys.argv:
                threading.Thread(target=ctl.request_quit).start()
            else:
                ctl.request_quit()
        QTimer.singleShot(150, request)
        if '--modal' in sys.argv:
            QMessageBox.information(window, 'Synthetic update', 'Synthetic update shutdown check')
            result['modal_returned'] = True
    QTimer.singleShot(100, exercise)
    QTimer.singleShot(4000, lambda: QCoreApplication.exit(3))
    code = app.exec()
    assert code == 0, 'Explicit quit was blocked'
    assert result['closed_to_tray'] and result['quit'] and window._force_close
    assert '--modal' not in sys.argv or result['modal_returned']
    print('PASS X hides; explicit quit exits; modal/worker handling verified')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
