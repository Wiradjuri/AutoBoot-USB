"""macOS read-only physical USB discovery using Apple's diskutil plist output."""

from __future__ import annotations

import platform
import plistlib
import subprocess

from ..models import UsbDevice
from .base import PlatformAdapter, UsbDiscoveryError


def parse_diskutil(info: dict, device_name: str) -> UsbDevice:
    # Treat anything other than explicitly external USB as non-selectable.
    bus = str(info.get("BusProtocol", "")).strip().lower()
    return UsbDevice(
        device_id=str(info.get("DeviceNode") or f"/dev/{device_name}"),
        name=str(info.get("MediaName") or info.get("DeviceName") or device_name),
        size_bytes=int(info.get("TotalSize", 0) or 0),
        bus=bus,
        removable=(info.get("Internal") is False and bus == "usb"),
        system_disk=bool(info.get("Bootable") and info.get("Internal") is not False),
        serial=str(info.get("DiskUUID") or ""),
        read_only=bool(info.get("ReadOnlyMedia", False)),
    )


class MacOSAdapter(PlatformAdapter):
    def list_usb_disks(self) -> list[UsbDevice]:
        try:
            disks = subprocess.run(
                ["diskutil", "list", "-plist", "external", "physical"],
                capture_output=True,
                timeout=12,
                check=True,
            )
            listing = plistlib.loads(disks.stdout)
            devices = []
            for disk in listing.get("AllDisksAndPartitions", []):
                disk_name = str(disk.get("DeviceIdentifier", ""))
                if not disk_name:
                    continue
                detail = subprocess.run(
                    ["diskutil", "info", "-plist", f"/dev/{disk_name}"],
                    capture_output=True,
                    timeout=12,
                    check=True,
                )
                device = parse_diskutil(plistlib.loads(detail.stdout), disk_name)
                if device.safe_to_offer:
                    devices.append(device)
            return devices
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            plistlib.InvalidFileException,
        ) as exc:
            raise UsbDiscoveryError(f"macOS could not enumerate external disks: {exc}") from exc

    def firmware_mode(self) -> str:
        return (
            "Apple Silicon startup security"
            if platform.machine() == "arm64"
            else "Intel Mac firmware"
        )

    def boot_guidance(self) -> str:
        if platform.machine() == "arm64":
            return (
                "Shut down the Mac. Hold the power button until startup options appear. "
                "External operating systems must support Apple Silicon and Apple's startup "
                "security requirements; a generic x86-64 Kali ISO will not boot this Mac."
            )
        return (
            "Restart your Intel Mac and hold Option (⌥) to open Startup Manager. "
            "Choose a compatible USB boot entry. On Macs with a T2 chip, external boot "
            "may first need to be enabled in Startup Security Utility."
        )
