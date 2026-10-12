"""AutoBoot USB application entry point."""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .services.state import configure_logging
from .ui.window import AutoBootWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AutoBoot USB")
    app.setApplicationDisplayName("AutoBoot USB")
    app.setOrganizationName("AutoBoot USB")
    app.setQuitOnLastWindowClosed(True)
    font = QFont()
    if sys.platform == "win32":
        font.setFamily("Segoe UI")
    elif sys.platform == "darwin":
        font.setFamily("Helvetica Neue")
    else:
        font.setFamily("Noto Sans")
    font.setPointSize(10)
    app.setFont(font)
    configure_logging()
    window = AutoBootWindow()
    window.show()
    return app.exec()
