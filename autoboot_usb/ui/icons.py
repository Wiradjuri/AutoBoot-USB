"""Purpose-built small glyphs instead of decorative emoji or icon packs."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap


def os_mark(mark: str, color: str, size: int = 30) -> QIcon:
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(0, 0, size, size), 5, 5)
    painter.setPen(QColor("#FFFFFF"))
    font = QFont("Segoe UI", 12 if len(mark) <= 1 else 7)
    font.setWeight(QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, mark)
    painter.end()
    return QIcon(pix)


def product_icon(size: int = 48) -> QIcon:
    """Amber-tipped USB mark, drawn in Qt and suitable for scaling in the title bar."""
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2, 2)
    p.setBrush(QColor("#E6B45D"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(size * 0.29, size * 0.34, size * 0.42, size * 0.54), 4, 4)
    p.drawRoundedRect(QRectF(size * 0.38, size * 0.13, size * 0.24, size * 0.27), 2, 2)
    p.setPen(QPen(QColor("#222A31"), 1.7))
    for x in (0.44, 0.56):
        p.drawLine(int(size * x), int(size * 0.2), int(size * x), int(size * 0.26))
    p.setPen(QPen(QColor("#222A31"), 2.3))
    p.drawLine(int(size * 0.4), int(size * 0.53), int(size * 0.6), int(size * 0.53))
    p.drawLine(int(size * 0.4), int(size * 0.65), int(size * 0.6), int(size * 0.65))
    p.end()
    return QIcon(pix)
