"""Platform adapter contract. Privileged USB writes are deliberately not in this interface yet."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import UsbDevice


class UsbDiscoveryError(RuntimeError):
    """The host OS could not enumerate storage devices."""


class PlatformAdapter(ABC):
    @abstractmethod
    def list_usb_disks(self) -> list[UsbDevice]:
        """Read-only scan of externally attached USB physical disks."""

    @abstractmethod
    def firmware_mode(self) -> str:
        """Current host firmware mode, if OS exposes this information."""

    @abstractmethod
    def boot_guidance(self) -> str:
        """Human-readable manual boot instructions, not an automated promise."""
