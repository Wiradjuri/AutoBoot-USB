"""Windows read-only disk discovery via the Microsoft Storage cmdlets."""

from __future__ import annotations

import ctypes
import json

from ..models import UsbDevice
from .base import PlatformAdapter, UsbDiscoveryError
from .windows_tools import powershell


class DiscoveryPermissionRequired(UsbDiscoveryError):
    pass


# This is strictly read-only. It never invokes Clear-Disk/Format-Volume/etc.
_READ_USB_DISKS = r"""
$ErrorActionPreference = 'Stop'
try {
$devices = @(Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Disk -Filter 'BusType = 7' -OperationTimeoutSec 60 | ForEach-Object {
    [PSCustomObject]@{
        Id        = "PhysicalDrive$($_.Number)"
        Name      = [string]$_.FriendlyName
        SizeBytes = [int64]$_.Size
        Bus       = 'USB'
        IsSystem  = [bool]$_.IsSystem
        IsBoot    = [bool]$_.IsBoot
        ReadOnly  = [bool]$_.IsReadOnly
        Serial    = [string]$_.SerialNumber
        UniqueId  = [string]$_.UniqueId
        Sector    = [int64]$_.LogicalSectorSize
    }
})
ConvertTo-Json -InputObject $devices -Compress -Depth 4
} catch {
 $kind='storage'; $e=$_.Exception
 while($e){
  if($e -is [UnauthorizedAccessException] -or [string]$e.NativeErrorCode -eq 'AccessDenied' -or
     $e.HResult -eq -2147024891 -or $e.HResult -eq -2147217405){$kind='access_denied';break}
  $e=$e.InnerException
 }
 @{AutoBootError=$kind;Message=[string]$_.Exception.Message} | ConvertTo-Json -Compress
}
"""


def parse_windows_disks(data: object) -> list[UsbDevice]:
    if not data:
        return []
    records = data if isinstance(data, list) else [data]
    devices = []
    for item in records:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "USB storage device").strip()
        devices.append(
            UsbDevice(
                device_id=str(item.get("Id") or ""),
                name=name,
                size_bytes=int(item.get("SizeBytes") or 0),
                bus=str(item.get("Bus") or "").lower(),
                removable=str(item.get("Bus") or "").lower() == "usb",
                system_disk=bool(item.get("IsSystem") or item.get("IsBoot")),
                serial=str(item.get("Serial") or "").strip(),
                read_only=bool(item.get("ReadOnly")),
                unique_id=str(item.get("UniqueId") or "").strip(),
                sector_size=int(item.get("Sector") or 512),
            )
        )
    return [d for d in devices if d.device_id and d.safe_to_offer]


class WindowsAdapter(PlatformAdapter):
    def list_usb_disks(self, cancel=None) -> list[UsbDevice]:
        try:
            raw = powershell(_READ_USB_DISKS, {}, cancel, timeout=90)
            data = json.loads(raw or "[]")
            if isinstance(data, dict) and "AutoBootError" in data:
                if data["AutoBootError"] == "access_denied":
                    raise DiscoveryPermissionRequired(
                        "Windows needs administrator access to read USB disks"
                    )
                raise UsbDiscoveryError(str(data.get("Message", "Windows storage provider failed")))
            return parse_windows_disks(data)
        except TimeoutError as exc:
            raise UsbDiscoveryError(
                "Windows storage did not answer the USB scan within 90 seconds. "
                "Reconnect the USB and select Refresh drives."
            ) from exc
        except (OSError, ValueError) as exc:
            raise UsbDiscoveryError(f"Windows could not read USB disk information: {exc}") from exc

    def firmware_mode(self) -> str:
        # GetFirmwareType is available on modern Windows; no elevation is required.
        try:
            firmware = ctypes.c_uint(0)
            fn = ctypes.windll.kernel32.GetFirmwareType
            fn.argtypes = [ctypes.POINTER(ctypes.c_uint)]
            fn.restype = ctypes.c_bool
            if fn(ctypes.byref(firmware)):
                return {1: "Legacy BIOS", 2: "UEFI"}.get(firmware.value, "Unknown")
        except (AttributeError, OSError, ValueError):
            pass
        return "Unknown"

    def boot_guidance(self) -> str:
        return (
            "Keep the USB connected and restart your PC. During startup, open your computer's "
            "one-time boot menu (often F12, F11, Esc or F8; the actual key depends on the "
            "manufacturer). Select the entry labelled UEFI USB or the USB drive's name. "
            "Alternatively, Windows Advanced startup may offer 'Use a device'."
        )
