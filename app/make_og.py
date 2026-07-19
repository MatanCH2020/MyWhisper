"""Generate docs/og-image.png — the branded 1200x630 social/SEO preview card
(app icon + name + tagline on the dark brand background), so link previews show
the icon, not a screenshot. Re-run after changing branding:

    .\\.venv\\Scripts\\python app\\make_og.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient,
                           QPainter, QRadialGradient)
from make_icon import draw as draw_icon

DOCS = Path(__file__).resolve().parent.parent / "docs"
W, H = 1200, 630


def _font(size, weight=QFont.Bold):
    f = QFont("Segoe UI")  # ships with Windows, covers Hebrew + Latin
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def main():
    app = QGuiApplication(sys.argv)
    img = QImage(W, H, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    # background gradient + top-center blue glow
    bg = QLinearGradient(0, 0, 0, H)
    bg.setColorAt(0, QColor("#0e121b"))
    bg.setColorAt(1, QColor("#0a0d13"))
    p.fillRect(0, 0, W, H, bg)
    glow = QRadialGradient(W / 2, 40, 640)
    glow.setColorAt(0, QColor(76, 130, 247, 90))
    glow.setColorAt(1, QColor(76, 130, 247, 0))
    p.fillRect(0, 0, W, H, glow)

    # app icon (rounded blue square) centered near the top
    icon = draw_icon(200)
    p.drawImage(int(W / 2 - 100), 96, icon)

    # title
    p.setPen(QColor("#f4f7fc"))
    p.setFont(_font(86, QFont.Black))
    p.drawText(QRectF(0, 300, W, 110), Qt.AlignHCenter | Qt.AlignVCenter, "MyWhisper")

    # tagline (Hebrew, RTL handled by Qt bidi)
    p.setPen(QColor("#aeb8c6"))
    p.setFont(_font(38, QFont.Medium))
    p.drawText(QRectF(0, 412, W, 56), Qt.AlignHCenter | Qt.AlignVCenter,
               "תמלול עברית מקומי ל-Windows")

    # accent sub-line
    p.setPen(QColor("#7aa5ff"))
    p.setFont(_font(30, QFont.DemiBold))
    p.drawText(QRectF(0, 486, W, 46), Qt.AlignHCenter | Qt.AlignVCenter,
               "חינם · פרטי · רץ על המחשב שלך")

    # bottom brand line
    p.setPen(QColor("#5b6577"))
    p.setFont(_font(24, QFont.Normal))
    p.drawText(QRectF(0, 556, W, 40), Qt.AlignHCenter | Qt.AlignVCenter,
               "Matan Digital  ·  github.com/MatanCH2020/MyWhisper")

    p.end()
    DOCS.mkdir(exist_ok=True)
    out = DOCS / "og-image.png"
    img.save(str(out), "PNG")
    print(f"wrote {out} ({W}x{H}, {out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
