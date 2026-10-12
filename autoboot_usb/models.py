"""Pure, testable data types. No Qt/platform imports in the core model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Release:
    name: str
    version: str
    edition: str
    target_architecture: str
    filename: str
    url: str
    sha256: str
    min_size_bytes: int
    # A release is only offered for direct download if its hash is pinned in this build.
    image_type: str = "hybrid-iso"
    checksum_url: str = ""
    size_bytes: int = 0
    provider: str = "direct"
    verification_digest: str = ""
    verification_algorithm: str = "sha256"

    @property
    def download_available(self) -> bool:
        if self.provider == "apple":
            return True
        return self.validated and (
            (bool(self.url) and bool(self.checksum_url)) or self.provider == "microsoft"
        )

    @property
    def validated(self) -> bool:
        return len(self.sha256) == 64 and all(c in "0123456789abcdef" for c in self.sha256)


@dataclass(frozen=True)
class OperatingSystem:
    key: str
    name: str
    subtitle: str
    brand_color: str
    mark: str
    official_page: str
    releases: tuple[Release, ...] = ()
    support_message: str = "Import an existing ISO. USB writing requires a tested image handler."


@dataclass(frozen=True)
class UsbDevice:
    device_id: str
    name: str
    size_bytes: int
    bus: str
    removable: bool
    system_disk: bool
    serial: str = ""
    read_only: bool = False
    unique_id: str = ""
    sector_size: int = 512

    @property
    def safe_to_offer(self) -> bool:
        # External USB mass-storage only. System disks are always excluded.
        return (
            self.bus.lower() == "usb"
            and self.removable
            and not self.system_disk
            and not self.read_only
            and self.size_bytes > 0
            # The writer also refuses Windows disk zero; hide it in the UI.
            and self.device_id.casefold() != "physicaldrive0"
        )


def format_size(size: int) -> str:
    if size < 0:
        raise ValueError("Size must not be negative")
    if size < 1_000:
        return f"{size} B"
    if size < 1_000_000:
        return f"{size / 1_000:.1f} KB"
    if size < 1_000_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size < 1_000_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    return f"{size / 1_000_000_000_000:.1f} TB"


def enough_capacity(device: UsbDevice, image_path: Path) -> bool:
    """A raw disk image needs no less than its length in writable drive capacity."""
    return device.size_bytes >= image_path.stat().st_size


@dataclass(frozen=True)
class ImageSelection:
    path: Path
    source: str  # "official" or "imported"
    computed_sha256: Optional[str] = None
    trusted_sha256: Optional[str] = None

    @property
    def verified(self) -> bool:
        return (
            self.trusted_sha256 is not None
            and len(self.trusted_sha256) == 64
            and all(c in "0123456789abcdef" for c in self.trusted_sha256)
            and self.computed_sha256 == self.trusted_sha256
        )
