"""Reusable Qt widgets for the MyWhisper UI: a rounded frameless window with a
drop shadow and native edge-resize, a branded title bar, a side nav rail, an
iOS-style toggle switch, and a card frame.
"""
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget, QCheckBox,
)

import icons
import theme


class FramelessWindow(QWidget):
    """Top-level window with no OS frame: rounded corners, soft shadow, and
    native edge-resize / move (via Qt's startSystemResize / startSystemMove so
    Windows snap and resize cursors keep working)."""

    GRAB = 6

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._m = 14  # shadow margin

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._m, self._m, self._m, self._m)
        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setMouseTracking(True)
        outer.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(0, 0, 0, 130))
        self.container.setGraphicsEffect(shadow)

        self.body = QVBoxLayout(self.container)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)

    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            m = 0 if self.isMaximized() else self._m
            self.layout().setContentsMargins(m, m, m, m)
        super().changeEvent(e)

    # ---- native resize ----
    def _edges_at(self, p):
        if self.isMaximized():
            return None
        g = self.GRAB + self._m
        w, h = self.width(), self.height()
        edges = Qt.Edges()
        if p.x() <= g:
            edges |= Qt.LeftEdge
        if p.x() >= w - g:
            edges |= Qt.RightEdge
        if p.y() <= g:
            edges |= Qt.TopEdge
        if p.y() >= h - g:
            edges |= Qt.BottomEdge
        return edges if int(edges) else None

    def _apply_cursor(self, edges):
        if not edges:
            self.unsetCursor()
            return
        l, r = bool(edges & Qt.LeftEdge), bool(edges & Qt.RightEdge)
        t, b = bool(edges & Qt.TopEdge), bool(edges & Qt.BottomEdge)
        if (l and t) or (r and b):
            self.setCursor(Qt.SizeFDiagCursor)
        elif (r and t) or (l and b):
            self.setCursor(Qt.SizeBDiagCursor)
        elif l or r:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.SizeVerCursor)

    def mouseMoveEvent(self, e):
        self._apply_cursor(self._edges_at(e.position().toPoint()))
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            edges = self._edges_at(e.position().toPoint())
            if edges and self.windowHandle():
                self.windowHandle().startSystemResize(edges)
                return
        super().mousePressEvent(e)


class TitleBar(QWidget):
    """Branded, draggable title bar with theme toggle + minimize/close."""

    def __init__(self, palette, on_theme, on_min, on_close):
        super().__init__()
        self.setObjectName("titlebar")
        self.setFixedHeight(48)
        self.setMouseTracking(True)
        self.setLayoutDirection(Qt.LeftToRight)  # logo left, window controls right
        p = palette
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(8)

        logo = QLabel()
        logo.setPixmap(icons.pixmap("mic", p["accent"], 20))
        lay.addWidget(logo)
        title = QLabel("MyWhisper")
        title.setFont(QFont(theme.pick_font(), 12, QFont.Bold))
        title.setStyleSheet(f"color:{p['text']};")
        lay.addWidget(title)
        sub = QLabel("· Matan Digital")
        sub.setObjectName("muted")
        lay.addWidget(sub)
        lay.addStretch(1)

        self._theme_btn = self._icon_btn("moon" if p["name"] == "light" else "sun",
                                         p["text_muted"], on_theme)
        for w in (self._theme_btn,
                  self._icon_btn("minimize", p["text_muted"], on_min),
                  self._icon_btn("close", p["text_muted"], on_close)):
            lay.addWidget(w)

    def _icon_btn(self, name, color, cb):
        b = QPushButton()
        b.setProperty("variant", "icon")
        b.setFixedSize(34, 30)
        b.setIcon(icons.icon(name, color, 18))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: cb())
        return b

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.window().toggle_max()


class NavRail(QWidget):
    """Vertical icon+label navigation; emits selected(index)."""

    selected = Signal(int)

    def __init__(self, palette, items):
        super().__init__()
        self.setObjectName("navrail")
        self.setFixedWidth(168)
        p = palette
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(4)
        self._btns = []
        for i, (name, label) in enumerate(items):
            b = QPushButton(f"  {label}")
            b.setObjectName("navitem")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setIcon(icons.icon(name, p["text_muted"], 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.selected.emit(idx))
            lay.addWidget(b)
            self._btns.append(b)
        lay.addStretch(1)
        if self._btns:
            self._btns[0].setChecked(True)

    def set_index(self, i):
        if 0 <= i < len(self._btns):
            self._btns[i].setChecked(True)


class ToggleSwitch(QCheckBox):
    """iOS-style toggle, custom-painted from the active palette."""

    def __init__(self, palette, checked=False):
        super().__init__()
        self._p = palette
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QColor(self._p["accent"] if on else self._p["border"])
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()),
                          self.height() / 2, self.height() / 2)
        d = self.height() - 6
        x = self.width() - d - 3 if on else 3
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, d, d))
        p.end()


class Card(QFrame):
    """Rounded surface panel with a vertical layout (use .vbox)."""

    def __init__(self, padding=14):
        super().__init__()
        self.setObjectName("card")
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(padding, padding, padding, padding)
        self.vbox.setSpacing(8)
