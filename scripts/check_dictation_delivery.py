"""Interactive Windows paste/foreground smoke test with synthetic text only.

Close the regular MyWhisper instance first so its clipboard watcher does not
record the synthetic test. This tests delivery, not microphone/ASR quality.
Click the button in the test window; both scenarios complete automatically.
"""
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock, patch

os.environ.pop("QT_QPA_PLATFORM", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
import main
from delivery import DestinationGuard

qapp = QApplication([])
qapp.setQuitOnLastWindowClosed(False)


class Check(QObject):
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.target = QWidget()
        self.target.setWindowTitle("MyWhisper — בדיקת הדבקה ללא מידע אישי")
        self.target.resize(640, 340)
        box = QVBoxLayout(self.target)
        box.addWidget(QLabel("בדיקת הדבקה אמיתית והגנה על יעד ההכתבה. טקסט הדגמה בלבד."))
        self.editor = QPlainTextEdit()
        box.addWidget(self.editor)
        self.button = QPushButton("התחל בדיקת הדבקה")
        self.button.clicked.connect(self.begin)
        box.addWidget(self.button)
        self.other = QPlainTextEdit()
        self.other.setWindowTitle("MyWhisper — יעד חלופי לבדיקה")
        self.other.resize(460, 260)
        self.finished.connect(self.completed)
        self.patches = [patch.object(main.history, "add"),
            patch.object(main.corrections, "bias_terms", return_value=""),
            patch.object(main.corrections, "english_terms", return_value=[]),
            patch.object(main.corrections, "apply", side_effect=lambda text: text)]
        self.saved = self.patches[0].start()
        for p in self.patches[1:]:
            p.start()
        self.app = main.Mywishper.__new__(main.Mywishper)
        QObject.__init__(self.app)
        self.app.ui, self.app.tray = Mock(), Mock()
        self.app.config = {"chatgpt_enabled": False, "bidi_isolate": False,
                           "restore_clipboard": True, "clipboard_restore_delay": .1}
        self.app.clipwatch = None
        self.app.transcriber = Mock()
        self.app.transcriber.transcribe.side_effect = lambda *_args, **_kw: (
            time.sleep(.4) or "MyWhisper delivery sample 123")
        self.app._worker_finished.connect(self.finished.emit)
        self.phase = 0
        self.target.show()

    def begin(self):
        self.button.setEnabled(False)
        self.editor.clear()
        self.editor.setFocus()
        QTimer.singleShot(100, self.run)

    def run(self):
        self.guard = DestinationGuard().start()
        threading.Thread(target=self.app._worker, args=([0], self.guard), daemon=True).start()
        if self.phase == 1:
            QTimer.singleShot(100, self.switch)

    def switch(self):
        self.other.show()
        self.other.raise_()
        self.other.activateWindow()

    def completed(self):
        self.guard.close()
        if self.phase == 0:
            passed = self.editor.toPlainText() == "MyWhisper delivery sample 123" and self.saved.call_count == 1
            print(f"Same-window native paste: {'PASS' if passed else 'FAIL'}", flush=True)
            if not passed:
                qapp.exit(1)
                return
            self.phase = 1
            self.editor.clear()
            self.target.raise_()
            self.target.activateWindow()
            self.editor.setFocus()
            QTimer.singleShot(150, self.run)
        else:
            passed = (not self.editor.toPlainText() and not self.other.toPlainText()
                      and self.saved.call_count == 2 and self.app.tray.notify.call_count == 1)
            print(f"Changed-window saves without paste: {'PASS' if passed else 'FAIL'}", flush=True)
            for p in self.patches:
                p.stop()
            qapp.exit(0 if passed else 1)


check = Check()
sys.exit(qapp.exec())
