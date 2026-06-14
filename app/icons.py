"""Lightweight, color-aware line icons drawn with QPainter (no extra deps).

icon(name, color, size) -> QIcon. Icons adapt to the active theme by passing the
theme's text/accent color.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _pen(p, color, s):
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.6, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _clock(p, s, color):
    _pen(p, color, s)
    m = s * 0.16
    p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
    c = s / 2
    p.drawLine(QPointF(c, c), QPointF(c, c - (s / 2 - m) * 0.55))
    p.drawLine(QPointF(c, c), QPointF(c + (s / 2 - m) * 0.4, c))


def _book(p, s, color):
    _pen(p, color, s)
    m = s * 0.16
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), s * 0.08, s * 0.08)
    x = m + (s - 2 * m) * 0.26
    p.drawLine(QPointF(x, m), QPointF(x, s - m))


def _sliders(p, s, color):
    _pen(p, color, s)
    m = s * 0.16
    w = s - 2 * m
    for i, (y, kx) in enumerate(((0.3, 0.62), (0.5, 0.36), (0.7, 0.6))):
        yy = m + w * y
        p.drawLine(QPointF(m, yy), QPointF(s - m, yy))
        p.setBrush(QColor(color))
        r = s * 0.07
        p.drawEllipse(QPointF(m + w * kx, yy), r, r)
        p.setBrush(Qt.NoBrush)


def _copy(p, s, color):
    _pen(p, color, s)
    m = s * 0.14
    d = s * 0.2
    p.drawRoundedRect(QRectF(m + d, m, s - m - (m + d), s - m - d - m + m), s * 0.06, s * 0.06)
    p.drawRoundedRect(QRectF(m, m + d, s - m - d - m + m, s - m - d), s * 0.06, s * 0.06)


def _trash(p, s, color):
    _pen(p, color, s)
    m = s * 0.18
    top = m + s * 0.08
    p.drawLine(QPointF(m, top), QPointF(s - m, top))           # lid
    hw = s * 0.14
    c = s / 2
    p.drawLine(QPointF(c - hw, top), QPointF(c - hw, m))       # handle
    p.drawLine(QPointF(c + hw, top), QPointF(c + hw, m))
    p.drawLine(QPointF(c - hw, m), QPointF(c + hw, m))
    bw = s * 0.13
    p.drawRoundedRect(QRectF(c - s * 0.22, top, s * 0.44, s - m - top),
                      s * 0.05, s * 0.05)                       # can
    for dx in (-bw, 0, bw):
        p.drawLine(QPointF(c + dx, top + s * 0.1), QPointF(c + dx, s - m - s * 0.06))


def _refresh(p, s, color):
    _pen(p, color, s)
    m = s * 0.18
    rect = QRectF(m, m, s - 2 * m, s - 2 * m)
    p.drawArc(rect, 60 * 16, 280 * 16)
    # arrowhead at the open end (~60deg)
    import math
    a = math.radians(60)
    cx, cy = s / 2, s / 2
    r = (s - 2 * m) / 2
    ex = cx + r * math.cos(a)
    ey = cy - r * math.sin(a)
    d = s * 0.13
    p.drawLine(QPointF(ex, ey), QPointF(ex - d, ey - d * 0.2))
    p.drawLine(QPointF(ex, ey), QPointF(ex + d * 0.2, ey + d))


def _search(p, s, color):
    _pen(p, color, s)
    m = s * 0.16
    d = (s - 2 * m) * 0.62
    p.drawEllipse(QRectF(m, m, d, d))
    p.drawLine(QPointF(m + d * 0.92, m + d * 0.92), QPointF(s - m, s - m))


def _mic(p, s, color):
    _pen(p, color, s)
    cw = s * 0.22
    cx = s / 2
    top = s * 0.16
    cap_h = s * 0.34
    p.drawRoundedRect(QRectF(cx - cw / 2, top, cw, cap_h), cw / 2, cw / 2)
    arc = QRectF(cx - s * 0.22, top + s * 0.06, s * 0.44, s * 0.44)
    p.drawArc(arc, 200 * 16, 140 * 16)
    p.drawLine(QPointF(cx, top + cap_h + s * 0.16), QPointF(cx, s - s * 0.16))
    p.drawLine(QPointF(cx - s * 0.14, s - s * 0.16), QPointF(cx + s * 0.14, s - s * 0.16))


def _sun(p, s, color):
    _pen(p, color, s)
    import math
    c = s / 2
    r = s * 0.18
    p.drawEllipse(QPointF(c, c), r, r)
    for i in range(8):
        a = math.radians(i * 45)
        r1 = r + s * 0.08
        r2 = r + s * 0.2
        p.drawLine(QPointF(c + r1 * math.cos(a), c + r1 * math.sin(a)),
                   QPointF(c + r2 * math.cos(a), c + r2 * math.sin(a)))


def _moon(p, s, color):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    outer = QPainterPath()
    outer.addEllipse(QRectF(s * 0.2, s * 0.16, s * 0.62, s * 0.62))
    inner = QPainterPath()
    inner.addEllipse(QRectF(s * 0.36, s * 0.1, s * 0.6, s * 0.6))
    p.drawPath(outer.subtracted(inner))


def _minimize(p, s, color):
    _pen(p, color, s)
    p.drawLine(QPointF(s * 0.28, s * 0.6), QPointF(s * 0.72, s * 0.6))


def _close(p, s, color):
    _pen(p, color, s)
    m = s * 0.3
    p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
    p.drawLine(QPointF(s - m, m), QPointF(m, s - m))


_DRAW = {
    "history": _clock, "clock": _clock, "dictionary": _book, "book": _book,
    "settings": _sliders, "copy": _copy, "trash": _trash, "refresh": _refresh,
    "search": _search, "mic": _mic, "sun": _sun, "moon": _moon,
    "minimize": _minimize, "close": _close,
}


def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    scale = 2  # render @2x for crisp HiDPI
    s = size * scale
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    draw = _DRAW.get(name)
    if draw:
        draw(p, s, color)
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str, size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))
