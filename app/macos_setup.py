"""Non-modal Hebrew setup, permission status, model download and retry."""
import platform
import subprocess
import threading
import time
from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QCheckBox
from macos_system import (accessibility_allowed, request_accessibility, microphone_status,
                         request_microphone, open_privacy, login_enabled, set_login_enabled)
from macos_clipboard import clipboard_allowed, request_clipboard
from model_download import DownloadCancelled


def system_problem():
    if platform.machine() != "arm64":
        return "גרסת הבטא מיועדת למעבדי Apple Silicon בלבד, ללא Rosetta."
    if int(platform.mac_ver()[0].split(".")[0]) < 15:
        return "גרסת הבטא דורשת macOS 15 ומעלה."
    memory = int(subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.memsize"], text=True))
    if memory < 16 * 1024 ** 3:
        return "גרסת הבטא הראשונה דורשת 16GB זיכרון ומעלה."
    return ""


class MacSetup(QDialog):
    progress = Signal(int)
    completed = Signal(str)

    def __init__(self, download, on_ready):
        super().__init__()
        self.download, self.on_ready = download, on_ready
        self.busy = False
        self.setWindowTitle("MyWhisper — הכנת ה־Mac")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        title = QLabel("תמלול עברית מקומי — בטא ל־Apple Silicon")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.permissions = QLabel()
        self.permissions.setWordWrap(True)
        layout.addWidget(self.permissions)
        for label, action in (("אפשר מיקרופון", self._microphone),
                              ("אפשר נגישות להדבקה", self._accessibility),
                              ("אפשר קריאת לוח העתקה", self._clipboard)):
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        note = QLabel("למעקב העתקות ברקע, בחר תמיד לאפשר בהגדרות הלוח של macOS, אם האפשרות קיימת. "
                      "ללא נגישות או קריאת לוח אפשר לתמלל ולשמור בהיסטוריה, אך ההדבקה עשויה להיחסם.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.download_status = QLabel()
        self.download_status.setWordWrap(True)
        layout.addWidget(self.download_status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)
        row = QHBoxLayout()
        self.start_button = QPushButton("הורד / המשך הורדת מודל (3.08GB)")
        self.start_button.clicked.connect(self.start_download)
        self.cancel_button = QPushButton("בטל הורדה")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)
        row.addWidget(self.start_button)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        retry = QPushButton("טען את המודל המקומי / נסה שוב")
        retry.clicked.connect(self.on_ready)
        layout.addWidget(retry)
        self.login = QCheckBox("הפעל ברקע בעת כניסה למחשב")
        self.login.setChecked(login_enabled())
        self.login.toggled.connect(self._login)
        layout.addWidget(self.login)
        self.progress.connect(self.bar.setValue)
        self.completed.connect(self._completed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def _microphone(self):
        if microphone_status() == 0:
            request_microphone()
        elif microphone_status() != 3:
            open_privacy("Microphone")

    def _accessibility(self):
        request_accessibility()
        if not accessibility_allowed():
            open_privacy("Accessibility")

    def _clipboard(self):
        request_clipboard()
        if not clipboard_allowed():
            open_privacy("Pasteboard")

    def _login(self, enabled):
        if not set_login_enabled(enabled):
            self.login.blockSignals(True)
            self.login.setChecked(login_enabled())
            self.login.blockSignals(False)
            self.download_status.setText("יש להעביר את האפליקציה ל־Applications לפני הפעלה בכניסה.")

    def refresh(self):
        mic = microphone_status() == 3
        self.permissions.setText("מיקרופון: " + ("מאושר" if mic else "נדרשת הרשאה") +
            "\nנגישות: " + ("מאושרת" if accessibility_allowed() else "נדרשת הרשאה להדבקה") +
            "\nלוח העתקה: " + ("זמין" if clipboard_allowed() else "נדרשת הרשאה למעקב ברקע"))
        if not self.busy and self.download.ready():
            self.bar.setValue(100)
            self.start_button.setEnabled(False)
            self.download_status.setText("המודל המקומי הורד ואומת. אין צורך באינטרנט לתמלול.")

    def start_download(self):
        if self.busy:
            return
        self.busy = True
        self.download.cancelled.clear()
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.download_status.setText("מוריד ומאמת את המודל. אפשר לבטל ולהמשיך מאוחר יותר.")
        def work():
            last = [0.0]
            def report(done, total):
                now = time.monotonic()
                if now - last[0] >= 0.1 or done == total:
                    self.progress.emit(int(100 * done / total))
                    last[0] = now
            try:
                self.download.download(report)
                message = ""
            except DownloadCancelled:
                message = "ההורדה בוטלה; ההתקדמות נשמרה להמשך."
            except Exception:
                message = "ההורדה או אימות המודל נכשלו. בדוק חיבור ומקום פנוי ונסה שוב."
            self.completed.emit(message)
        threading.Thread(target=work, daemon=True).start()

    def cancel_download(self):
        self.download.cancelled.set()
        self.download_status.setText("מבטל את ההורדה…")

    def _completed(self, error):
        self.busy = False
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.download_status.setText(error)
        if not error:
            self.refresh()
            self.on_ready()

    def closeEvent(self, event):
        if self.busy:
            self.cancel_download()
        super().closeEvent(event)
