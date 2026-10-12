"""Standalone packaging entry-point for PyInstaller (imports work outside the package)."""

from autoboot_usb.main import main

if __name__ == "__main__":
    raise SystemExit(main())
