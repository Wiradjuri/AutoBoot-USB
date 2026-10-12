"""Linux read-only discovery via lsblk; requires util-linux on the host."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..models import UsbDevice
from .base import PlatformAdapter, UsbDiscoveryError

SYSTEM_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/home", "/usr", "/var", "/swap"}


def _has_system_mount(record: dict) -> bool:
    mounts = record.get("mountpoints") or []
    if record.get("mountpoint"):
        mounts = [*mounts, record["mountpoint"]]
    if any(m in SYSTEM_MOUNTPOINTS for m in mounts if m):
        return True
    return any(_has_system_mount(child) for child in record.get("children", []))


def parse_lsblk(data: dict) -> list[UsbDevice]:
    devices: list[UsbDevice] = []
    for item in data.get("blockdevices", []):
        if item.get("type") != "disk" or (item.get("tran") or "").lower() != "usb":
            continue
        device = UsbDevice(
            device_id=str(item.get("path") or "/dev/" + str(item.get("name") or "")),
            name=str(item.get("model") or item.get("name") or "USB storage device").strip(),
            size_bytes=int(item.get("size") or 0),
            bus="usb",
            removable=True,
            system_disk=_has_system_mount(item),
            serial=str(item.get("serial") or "").strip(),
            read_only=bool(int(item.get("ro") or 0)),
        )
        if device.safe_to_offer:
            devices.append(device)
    return devices


class LinuxAdapter(PlatformAdapter):
    def list_usb_disks(self) -> list[UsbDevice]:
        try:
            process = subprocess.run(
                [
                    "lsblk",
                    "--json",
                    "--bytes",
                    "--output",
                    "NAME,PATH,TYPE,TRAN,RM,HOTPLUG,RO,SIZE,MODEL,SERIAL,MOUNTPOINTS",
                ],
                capture_output=True,
                text=True,
                timeout=12,
                check=True,
            )
            return parse_lsblk(json.loads(process.stdout))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise UsbDiscoveryError(f"Linux could not enumerate USB drives: {exc}") from exc

    def firmware_mode(self) -> str:
        return "UEFI" if Path("/sys/firmware/efi").exists() else "Legacy BIOS or virtual machine"

    def boot_guidance(self) -> str:
        return (
            "Restart the computer with the USB drive connected. Open its one-time boot menu "
            "using the manufacturer's startup key (often F12, F11 or Esc) and choose the "
            "USB entry. Secure Boot support varies by distribution and firmware."
        )
