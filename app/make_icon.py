"""Generate app/assets/icon.ico — the MyWhisper app icon.

A white microphone on a rounded brand-blue gradient square, rendered with
QPainter at every standard size and packed into a single .ico (PNG-compressed
entries, supported since Vista). Run once after changing the design:

    .\\.venv\\Scripts\\python app\\make_icon.py
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

ASSETS = Path(__file__).resolve().parent / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    n = float(size)

    # Rounded brand-blue square.
    grad = QLinearGradient(0, 0, n, n)
    grad.setColorAt(0.0, QColor("#4C82F7"))
    grad.setColorAt(1.0, QColor("#2456C4"))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, n, n), n * 0.22, n * 0.22)

    # White microphone.
    white = QColor("#FFFFFF")
    stroke = max(1.0, n * 0.055)
    pen = QPen(white, stroke, Qt.SolidLine, Qt.RoundCap)

    # capsule body
    p.setBrush(white)
    p.setPen(Qt.NoPen)
    body = QRectF(n * 0.395, n * 0.20, n * 0.21, n * 0.34)
    p.drawRoundedRect(body, body.width() / 2, body.width() / 2)

    # cradle arc (bottom half circle around the capsule)
    p.setBrush(Qt.NoBrush)
    p.setPen(pen)
    arc_rect = QRectF(n * 0.30, n * 0.26, n * 0.40, n * 0.40)
    p.drawArc(arc_rect, 180 * 16, 180 * 16)

    # stem + base
    p.drawLine(QPointF(n * 0.50, n * 0.66), QPointF(n * 0.50, n * 0.755))
    p.drawLine(QPointF(n * 0.40, n * 0.775), QPointF(n * 0.60, n * 0.775))

    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, images: dict):
    """Pack {size: png_bytes} into an .ico (PNG entries)."""
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size in sorted(images):
        data = images[size]
        entries.append(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,   # width (0 means 256)
            size if size < 256 else 0,   # height
            0, 0,                        # palette, reserved
            1, 32,                       # planes, bit depth
            len(data), offset))
        blobs.append(data)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(images)))  # ICONDIR
        f.write(b"".join(entries))
        f.write(b"".join(blobs))


def main():
    QGuiApplication(sys.argv)  # QPainter needs a Gui application instance
    ASSETS.mkdir(exist_ok=True)
    out = ASSETS / "icon.ico"
    write_ico(out, {s: png_bytes(draw(s)) for s in SIZES})
    print(f"wrote {out} ({out.stat().st_size} bytes, sizes {SIZES})")


if __name__ == "__main__":
    main()
