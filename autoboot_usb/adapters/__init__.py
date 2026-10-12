"""Identify the current host platform and expose a read-only storage adapter."""

import sys

from .base import PlatformAdapter


def get_adapter() -> PlatformAdapter:
    if sys.platform == "win32":
        from .windows import WindowsAdapter

        return WindowsAdapter()
    if sys.platform == "darwin":
        from .macos import MacOSAdapter

        return MacOSAdapter()
    if sys.platform.startswith("linux"):
        from .linux import LinuxAdapter

        return LinuxAdapter()
    raise RuntimeError(f"Unsupported host OS: {sys.platform}")
