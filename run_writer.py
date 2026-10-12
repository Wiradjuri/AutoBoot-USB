"""Packaging entry point for the separately elevated writer, without Qt."""

import sys
from pathlib import Path

# -I strips the script directory from sys.path. Only restore this trusted root.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from autoboot_usb.services.elevated_writer import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
