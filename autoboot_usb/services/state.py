"""Non-sensitive UI preferences and structured application log location."""

from __future__ import annotations

import logging
import platform
from pathlib import Path

from PySide6.QtCore import QStandardPaths


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
