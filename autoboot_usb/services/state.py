"""Non-sensitive UI preferences and structured application log location."""

from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def image_directory(system_key: str) -> Path:
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", system_key):
        raise ValueError("Invalid operating system folder")
    folder = application_directory() / "Images" / system_key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def user_data_directory() -> Path:
    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not loc:
        raise RuntimeError("Cannot locate a writable user application-data directory")
    path = Path(loc)
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging() -> Path:
    logfile = user_data_directory() / "autoboot.log"
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(
        logfile, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            '%(asctime)s %(levelname)s component=%(name)s host="%(host)s" message=%(message)s'
        )
    )

    class HostFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.host = platform.system()
            return True

    handler.addFilter(HostFilter())
    root = logging.getLogger("autoboot_usb")
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)
    return logfile
