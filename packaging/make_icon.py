"""Render the application's existing USB mark into a Windows executable icon."""

import sys
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from autoboot_usb.ui.icons import product_icon

app = QApplication([])
folder = Path(sys.argv[1])
folder.mkdir(parents=True, exist_ok=True)
png = folder / "AutoBootUSB.png"
if not product_icon(256).pixmap(256, 256).save(str(png)):
    raise RuntimeError("Could not render product icon")
with Image.open(png) as image:
    image.save(folder / "AutoBootUSB.ico", sizes=[(n, n) for n in (16, 32, 48, 64, 128, 256)])
