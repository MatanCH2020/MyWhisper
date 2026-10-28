"""Support form: local preview first, explicit submission through injected callbacks."""
import json
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

import theme
from support import CATEGORIES, ReportError, clean_diagnostics, make_report


class SupportDialog(QDialog):
    _finished = Signal(object, object)

    def __init__(self, parent, palette, get_diagnostics, send_report):
        super().__init__(parent)
        self.setWindowTitle("דיווח על תקלה — MyWhisper")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(560, 690)
        self.setMinimumSize(430, 540)
        theme.bind_style(self, lambda p: f"QDialog{{background:{p['bg']};}}", palette)
        self._send = send_report
        self._busy = False
        self._payload = None
        self._technical = clean_diagnostics(get_diagnostics())
        self._finished.connect(self._on_finished)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel("הדיווח נשלח לצוות Matan Digital רק בלחיצה על שליחה.\n"
                       "אין צירוף אוטומטי של תמלולים, הקלטות, תוכן לוח או לוגים. "
                       "בדוק שאין מידע אישי בתיאור שתכתוב.")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        layout.addWidget(intro)
        layout.addWidget(QLabel("באיזה תחום התקלה?"))
        self.category = QComboBox()
        self.category.setAccessibleName("תחום התקלה")
        for key, label in CATEGORIES.items():
            self.category.addItem(label, key)
        layout.addWidget(self.category)
        layout.addWidget(QLabel("מה קרה ומה ציפית שיקרה?"))
        self.description = QPlainTextEdit()
        self.description.setAccessibleName("תיאור התקלה")
        self.description.setPlaceholderText("לפחות 10 תווים. אין צורך לצרף תוכן שהכתבת או העתקת.")
        layout.addWidget(self.description, 2)
        self.contact = QLineEdit()
        self.contact.setMaxLength(254)
        self.contact.setPlaceholderText("פרטי קשר לתשובה — לא חובה")
        self.contact.setAccessibleName("פרטי קשר לתשובה, לא חובה")
        layout.addWidget(self.contact)
        layout.addWidget(QLabel("הנתונים הטכניים שיצורפו — תצוגה מלאה"))
        self.preview = QPlainTextEdit(json.dumps(self._technical, ensure_ascii=False, indent=2))
        self.preview.setReadOnly(True)
        self.preview.setLayoutDirection(Qt.LeftToRight)
        self.preview.setAccessibleName("תצוגה מקדימה של הנתונים הטכניים שיישלחו")
        layout.addWidget(self.preview, 1)
        self.status = QLabel("")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("hint")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.send_button = QPushButton("שלח דיווח")
        self.send_button.clicked.connect(self._submit)
        self.close_button = QPushButton("ביטול")
        self.close_button.clicked.connect(self.reject)
        self.close_button.setDefault(True)
        self.send_button.setAutoDefault(False)
        row.addWidget(self.send_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)

    def _submit(self):
        if self._busy:
            return
        try:
            old_id = self._payload["request_id"] if self._payload else None
            payload = make_report(self.category.currentData(), self.description.toPlainText(),
                                  self.contact.text(), self._technical, old_id)
            if self._payload is not None and payload != self._payload:
                payload = make_report(payload["category"], payload["description"], payload["contact"], self._technical)
        except ReportError as error:
            self.status.setText(str(error))
            return
        self._payload = payload
        self._set_busy(True)
        self.status.setText("שולח וממתין לאישור שמירה…")

        def send():
            try:
                reference = self._send(payload)
                self._finished.emit(reference, None)
            except Exception as error:
                message = str(error) if isinstance(error, ReportError) else "השליחה לא אושרה. אפשר לנסות שוב."
                self._finished.emit(None, message)
        threading.Thread(target=send, daemon=True).start()

    def _set_busy(self, busy):
        self._busy = busy
        for widget in (self.category, self.description, self.contact, self.send_button, self.close_button):
            widget.setEnabled(not busy)

    def _on_finished(self, reference, error):
        self._set_busy(False)
        if error or not isinstance(reference, str) or not reference:
            self.status.setText(error or "לא התקבל מספר דיווח. אפשר לנסות שוב.")
            return
        self.status.setText(f"הדיווח נשמר. מספר דיווח: {reference}")
        self.send_button.setEnabled(False)
        for widget in (self.category, self.description, self.contact):
            widget.setEnabled(False)
        self.close_button.setText("סגירה")

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
