"""System tray icon (Qt) that reflects app state (idle / recording / transcribing).

QSystemTrayIcon is native and lives on the Qt main thread. set_state() is called
from worker threads, so it marshals to the main thread via a queued signal.
"""
from PySide6.QtCore import QObject, QPointF, QRectF, Signal, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

COLORS = {
    "idle": "#787878",        # grey
    "recording": "#dc2828",   # red
    "transcribing": "#e6be1e",  # yellow
}


def _make_icon(color_hex: str) -> QIcon:
    """Microphone silhouette in the given state color (brand shape, readable
    at 16px in the notification area)."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color_hex)
    # capsule body
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(24, 6, 16, 26), 8, 8)
    # cradle arc + stem + base
    pen = QPen(c, 6, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(14, 12, 36, 36), 180 * 16, 180 * 16)
    p.drawLine(QPointF(32, 48), QPointF(32, 56))
    p.drawLine(QPointF(22, 58), QPointF(42, 58))
    p.end()
    return QIcon(pm)


class Tray(QObject):
    """Wraps a QSystemTrayIcon; call set_state() (thread-safe) to change the dot."""

    _state_sig = Signal(str, str)        # (state, tooltip)
    _notify_sig = Signal(str, str, str)  # (title, message, level) -> balloon

    def __init__(self, on_quit, on_settings=None, hotkey="ctrl+alt+space"):
        super().__init__()
        self._icons = {s: _make_icon(c) for s, c in COLORS.items()}
        on_settings = on_settings or (lambda: None)

        self._tray = QSystemTrayIcon(self._icons["idle"])
        self._tray.setToolTip("MyWhisper — מוכן")

        menu = QMenu()
        menu.setLayoutDirection(Qt.RightToLeft)
        menu.addAction("הגדרות", lambda: on_settings())
        act_hotkey = menu.addAction(f"קיצור: {hotkey}")
        act_hotkey.setEnabled(False)
        menu.addSeparator()
        menu.addAction("יציאה", lambda: on_quit())
        self._tray.setContextMenu(menu)

        # Left-click (Trigger) opens settings.
        self._tray.activated.connect(
            lambda reason: on_settings()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

        self._state_sig.connect(self._apply_state)
        self._notify_sig.connect(self._show_message)
        self._tray.show()

    def _apply_state(self, state: str, tooltip: str):
        if state in self._icons:
            self._tray.setIcon(self._icons[state])
        if tooltip:
            self._tray.setToolTip(tooltip)

    def set_state(self, state: str, title: str = None):
        """Thread-safe: update the tray dot/tooltip from any thread."""
        self._state_sig.emit(state, title or "")

    def _show_message(self, title: str, msg: str, level: str):
        icon = (QSystemTrayIcon.MessageIcon.Warning if level == "warning"
                else QSystemTrayIcon.MessageIcon.Information)
        self._tray.showMessage(title, msg, icon, 8000)

    def notify(self, title: str, msg: str, level: str = "info"):
        """Thread-safe: show a balloon notification from any thread."""
        self._notify_sig.emit(title, msg, level)

    def stop(self):
        self._tray.hide()
