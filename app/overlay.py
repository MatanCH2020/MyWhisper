"""Non-activating recording HUD, anchored to the cursor's screen per session."""
import ctypes
import math
import sys
import time

from PySide6.QtCore import (QEasingCurve, QPoint, QRectF, Qt, QTimer,
                            QParallelAnimationGroup, QPropertyAnimation)
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

import icons
import theme


def motion_enabled():
    """Windows' client-area animation accessibility preference (SPI_GET...)."""
    if sys.platform == "win32":
        enabled = ctypes.c_int(1)
        try:
            if ctypes.windll.user32.SystemParametersInfoW(
                    0x1042, 0, ctypes.byref(enabled), 0):
                return bool(enabled.value)
        except (AttributeError, OSError):
            pass
    return True


def hud_position(available, size):
    """Qt logical coordinates; includes negative monitor origins and taskbars."""
    x = available.x() + (available.width() - size.width()) // 2
    y = available.y() + 32
    return QPoint(max(available.x(), min(x, available.right() - size.width() + 1)),
                  max(available.y(), min(y, available.bottom() - size.height() + 1)))


class Overlay(QWidget):
    CARD_W, CARD_H, SHADOW = 280, 100, 12

    def __init__(self, level_provider):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setWindowTitle("MyWhisper — Recording HUD")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(self.CARD_W + self.SHADOW * 2,
                          self.CARD_H + self.SHADOW * 2)
        self.level_provider = level_provider
        self.state = "idle"
        self.frame = 0
        self._started = 0.0
        self._elapsed = 0
        self._screen = None
        self._motion = True
        self._level = 0.0
        self._mic = icons.icon("mic", "#FF777D", 24)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._entrance = QParallelAnimationGroup(self)
        self._slide = QPropertyAnimation(self, b"pos", self._entrance)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self._entrance)
        for animation in (self._slide, self._fade):
            animation.setDuration(220)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            self._entrance.addAnimation(animation)
        QApplication.instance().screenRemoved.connect(self._screen_removed)

    def _choose_screen(self, excluded=None):
        screen = QApplication.screenAt(QCursor.pos())
        if screen is None or screen == excluded:
            screen = QApplication.primaryScreen()
        if screen == excluded:
            screen = next((s for s in QApplication.screens() if s != excluded), None)
        return screen

    def _bind_screen(self, screen):
        if self._screen is not None:
            try:
                self._screen.availableGeometryChanged.disconnect(self._reposition)
            except (RuntimeError, TypeError):
                pass  # The native screen may already have been destroyed.
        self._screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._reposition)

    def _reposition(self, *_):
        if self._screen is not None and self.state != "idle":
            self._entrance.stop()
            self.setWindowOpacity(1.0)
            self.move(hud_position(self._screen.availableGeometry(), self.size()))

    def _screen_removed(self, screen):
        if screen == self._screen:
            self._bind_screen(self._choose_screen(excluded=screen))
            self._reposition()

    def set_state(self, state):
        previous = self.state
        self.state = state if state in ("recording", "transcribing") else "idle"
        if self.state == "idle":
            self._timer.stop()
            self._entrance.stop()
            self.hide()
            self.setWindowOpacity(1.0)
            self._bind_screen(None)
            return
        new_recording = self.state == "recording" and previous != "recording"
        if new_recording:
            self._started = time.monotonic()
            self._elapsed = 0
            self._level = 0.0
        if previous == "idle" or new_recording:
            self.frame = 0
            self._motion = motion_enabled()
            self._bind_screen(self._choose_screen())
            self._reposition()
            target = self.pos()
            if self._motion:
                self.move(target - QPoint(0, 18))
                self.setWindowOpacity(0.0)
                self._slide.setStartValue(self.pos())
                self._slide.setEndValue(target)
                self._fade.setStartValue(0.0)
                self._fade.setEndValue(1.0)
            self.show()
            self.raise_()
            if self._motion:
                self._entrance.start()
        self.setAccessibleName("מקליט" if self.state == "recording" else "מתמלל")
        self._timer.start(33 if self._motion else 100)
        self.update()

    def _tick(self):
        self.frame += 1
        if self.state == "recording":
            self._elapsed = max(0, int(time.monotonic() - self._started))
            try:
                value = float(self.level_provider())
                target = max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0
            except (TypeError, ValueError, RuntimeError):
                target = 0.0
            self._level += (target - self._level) * 0.4
        self.update()

    def closeEvent(self, event):
        self.set_state("idle")
        super().closeEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.SHADOW
        card = QRectF(s, s, self.CARD_W, self.CARD_H)
        p.setPen(Qt.NoPen)
        for spread in range(10, 0, -2):
            p.setBrush(QColor(0, 0, 0, 6))
            p.drawRoundedRect(card.adjusted(-spread, -spread + 3, spread, spread + 3),
                              22 + spread, 22 + spread)
        p.setBrush(QColor("#1C1F29"))
        p.setPen(QPen(QColor("#424653"), 1))
        p.drawRoundedRect(card, 22, 22)
        recording = self.state == "recording"
        accent = QColor("#FF777D" if recording else "#F4CC77")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3B2832" if recording else "#3A3428"))
        p.drawRoundedRect(QRectF(s + 226, s + 14, 38, 38), 13, 13)
        if recording:
            self._mic.paint(p, s + 233, s + 21, 24, 24)
        else:
            p.setPen(QPen(accent, 2.5, Qt.SolidLine, Qt.RoundCap))
            angle = -(self.frame * 9 % 360) if self._motion else 90
            p.drawArc(QRectF(s + 237, s + 25, 16, 16), angle * 16, 265 * 16)
        font = QFont(theme.pick_font())
        font.setPixelSize(16)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#F5F6FA"))
        p.drawText(s + 88, s + 12, 126, 24, Qt.AlignRight | Qt.AlignVCenter,
                   "מקליט" if recording else "מתמלל")
        font.setPixelSize(12)
        font.setBold(False)
        p.setFont(font)
        p.setPen(QColor("#B9BFCE"))
        p.drawText(s + 78, s + 36, 136, 18, Qt.AlignRight | Qt.AlignVCenter,
                   "מקש Esc לביטול" if recording else "מעבד את ההקלטה")
        font.setPixelSize(18)
        p.setFont(font)
        p.setPen(accent)
        p.drawText(s + 18, s + 17, 68, 30, Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self._elapsed // 60:02d}:{self._elapsed % 60:02d}")
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        for i in range(29):
            phase = self.frame * 0.22 if self._motion else 0
            wave = (math.sin(phase + i * 0.55) + 1) / 2
            if recording:
                # Quiet microphone stays quiet: no artificial signal animation.
                h = 4 + self._level * 23 * (0.35 + 0.65 * wave)
            else:
                h = 4 + 17 * (wave if self._motion else 0.25)
            p.drawRoundedRect(QRectF(s + 26 + i * 8, s + 76 - h / 2, 4, h), 2, 2)
        p.end()
