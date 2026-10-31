"""Two explicit dictation modes, shared by the shell and non-activating HUD."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel


class ModeSelector(QWidget):
    selected = Signal(bool)

    def __init__(self, parent=None, *, nonactivating=False):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.local = QPushButton("מקומי")
        self.cloud = QPushButton("\u2066AI\u2069 בענן")
        for button, cloud in ((self.local, False), (self.cloud, True)):
            button.setLayoutDirection(Qt.RightToLeft)
            button.setObjectName("modebutton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus if nonactivating else Qt.StrongFocus)
            button.setMinimumHeight(32)
            button.clicked.connect(lambda checked=False, value=cloud: self.selected.emit(value))
            row.addWidget(button)
        layout.addLayout(row)
        self.detail = QLabel()
        self.detail.setLayoutDirection(Qt.RightToLeft)
        self.detail.setObjectName("modehint")
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.detail)
        self.update_status({})

    def update_status(self, status, *, busy=False, supported=True):
        cloud = status.get("enabled") is True
        model = status.get("model", "")
        models = {m["slug"]: m.get("display_name") or m["slug"] for m in status.get("models", [])}
        ready = (status.get("connected") and status.get("eligible") and model in models
                 and status.get("error") not in ("authorization", "identity", "permission", "ineligible", "storage"))
        for button, active in ((self.local, not cloud), (self.cloud, cloud)):
            button.setChecked(active)
        self.local.setText("מקומי · פעיל" if not cloud else "מקומי")
        self.cloud.setText("\u2066AI\u2069 בענן · פעיל" if cloud else "\u2066AI\u2069 בענן")
        self.cloud.setVisible(supported)
        self.local.setEnabled(not busy)
        self.cloud.setEnabled(not busy and bool(ready) and supported)
        self.cloud.setToolTip("אפשר לומר מה לנסח ולדבר בחופשיות. הטקסט נשלח ל־OpenAI; האודיו נשאר במחשב." if ready else
                             "חבר חשבון ובחר מודל בהגדרות כדי להשתמש בענן.")
        self.local.setToolTip("תמלול במחשב, ללא שליחת הטקסט לענן.")
        if cloud:
            detail = "הטקסט נשלח ל־\u2066OpenAI\u2069 · \u2066" + models.get(model, model) + "\u2069"
        else:
            detail = "הדיבור מתומלל במחשב בלבד"
            if not ready and supported:
                detail += " · להגדרת ענן: הגדרות"
        self.detail.setText(detail)
        self.setAccessibleName("מצב עבודה: " + ("AI בענן" if cloud else "מקומי"))
