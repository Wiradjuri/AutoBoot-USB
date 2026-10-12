"""AutoBoot USB application entry point."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtNetwork import QNetworkProxyFactory
from PySide6.QtWidgets import QApplication

from .services.state import configure_logging
from .ui.window import AutoBootWindow


def main() -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QNetworkProxyFactory.setUseSystemConfiguration(True)
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
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-check":
        from .packaging_check import run

        report = Path(sys.argv[2])
        try:
            result = run(app)
        except Exception:
            report.write_text(
                json.dumps({"status": "fail", "error": traceback.format_exc()}), encoding="utf-8"
            )
            return 1
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 0
    window = AutoBootWindow()
    window.show()
    return app.exec()
