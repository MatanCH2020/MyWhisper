"""Non-activating recording HUD, anchored to the cursor's screen per session."""
import ctypes
import math
import sys
import time

from PySide6.QtCore import (QEasingCurve, QPoint, QRectF, Qt, QTimer,
                            QParallelAnimationGroup, QPropertyAnimation)
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QPainter, QPen, QTextOption
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
    CARD_W, CARD_H, SHADOW = 240, 72, 8

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
        self.state = state if state in ("recording", "transcribing", "polishing") else "idle"
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
        self.setAccessibleName({"recording": "מקליט", "transcribing": "מתמלל",
                                "polishing": "מסדר טקסט…"}[self.state])
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

    def _text(self, painter, rect, text, pixels, color, *, rtl=False, bold=False):
        """Use physical alignment and explicit direction, independent of app RTL.

        Qt's flag-based drawText mirrors AlignLeft/Right in an RTL application.
        QTextOption plus AlignAbsolute keeps each run in its own reserved region.
        Measure the actual font so a fallback font cannot silently clip a label.
        """
        font = QFont(theme.pick_font())
        font.setPixelSize(pixels)
        font.setBold(bold)
        while font.pixelSize() > 12:
            metrics = QFontMetricsF(font)
            if metrics.horizontalAdvance(text) <= rect.width() and metrics.height() <= rect.height():
                break
            font.setPixelSize(font.pixelSize() - 1)
        option = QTextOption()
        option.setTextDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        option.setAlignment(Qt.AlignAbsolute | Qt.AlignVCenter |
                            (Qt.AlignRight if rtl else Qt.AlignLeft))
        option.setWrapMode(QTextOption.NoWrap)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, text, option)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.SHADOW
        card = QRectF(s, s, self.CARD_W, self.CARD_H)
        p.setPen(Qt.NoPen)
        for spread in range(6, 0, -2):
            p.setBrush(QColor(0, 0, 0, 6))
            p.drawRoundedRect(card.adjusted(-spread, -spread + 2, spread, spread + 2),
                              16 + spread, 16 + spread)
        p.setBrush(QColor("#1C1F29"))
        p.setPen(QPen(QColor("#424653"), 1))
        p.drawRoundedRect(card, 16, 16)
        recording = self.state == "recording"
        accent = QColor("#FF777D" if recording else "#F4CC77")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3B2832" if recording else "#3A3428"))
        p.drawRoundedRect(QRectF(s + 200, s + 10, 26, 26), 9, 9)
        if recording:
            self._mic.paint(p, s + 204, s + 14, 18, 18)
        else:
            p.setPen(QPen(accent, 2.5, Qt.SolidLine, Qt.RoundCap))
            angle = -(self.frame * 9 % 360) if self._motion else 90
            p.drawArc(QRectF(s + 205, s + 15, 16, 16), angle * 16, 265 * 16)
        # Top row: timer | 12px gap | title | 12px gap | icon.
        self._text(p, QRectF(s + 100, s + 10, 88, 26),
                   "מקליט" if recording else "מסדר טקסט…" if self.state == "polishing" else "מתמלל",
                   15, "#F5F6FA", rtl=True, bold=True)
        self._text(p, QRectF(s + 14, s + 10, 74, 26),
                   f"{self._elapsed // 60:02d}:{self._elapsed % 60:02d}", 16, accent)
        # Bottom row has a separate 94px waveform and a 106px Hebrew hint.
        self._text(p, QRectF(s + 120, s + 48, 106, 18),
                   "\u2066Esc\u2069 לביטול" if recording else
                   "עריכת OpenAI" if self.state == "polishing" else "מעבד את ההקלטה",
                   12, "#B9BFCE", rtl=True)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        for i in range(16):
            phase = self.frame * 0.22 if self._motion else 0
            wave = (math.sin(phase + i * 0.55) + 1) / 2
            if recording:
                # Quiet microphone stays quiet: no artificial signal animation.
                h = 3 + self._level * 11 * (0.35 + 0.65 * wave)
            else:
                h = 3 + 11 * (wave if self._motion else 0.25)
            p.drawRoundedRect(QRectF(s + 14 + i * 6, s + 57 - h / 2, 4, h), 2, 2)
        p.end()
