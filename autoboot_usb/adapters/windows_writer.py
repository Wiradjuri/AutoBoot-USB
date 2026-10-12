"""Win32 physical disk I/O, used only by a separately elevated process.

No shell-supplied device paths, diskpart scripts or format commands are accepted.
Mounted volumes stay locked through read-back. Ambiguous devices fail closed.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from ..models import UsbDevice
from ..services.writer import WriteSafetyError, validate_target
from .windows import WindowsAdapter

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_DISMOUNT_VOLUME = 0x00090020
FSCTL_UNLOCK_VOLUME = 0x0009001C
IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0
# ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED, ERROR_INVALID_PARAMETER: drivers
# that do not implement StorageAccessAlignmentProperty (common on USB bridges).
_ALIGNMENT_UNSUPPORTED = {1, 50, 87}


class WinApi:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Physical USB writing is implemented only for the Windows preview")
        from ctypes import wintypes as w

        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        definitions = {
            "CreateFileW": (
                [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE],
                w.HANDLE,
            ),
            "DeviceIoControl": (
                [
                    w.HANDLE,
                    w.DWORD,
                    w.LPVOID,
                    w.DWORD,
                    w.LPVOID,
                    w.DWORD,
                    ctypes.POINTER(w.DWORD),
                    w.LPVOID,
                ],
                w.BOOL,
            ),
            "ReadFile": ([w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL),
            "WriteFile": ([w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL),
            "SetFilePointerEx": (
                [w.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), w.DWORD],
                w.BOOL,
            ),
            "FlushFileBuffers": ([w.HANDLE], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "GetVolumePathNameW": ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.BOOL),
            "GetVolumeNameForVolumeMountPointW": ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.BOOL),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result

    def check(self, ok, action):
        if not ok:
            error = ctypes.get_last_error()
            raise OSError(error, f"{action}: {ctypes.FormatError(error).strip()}")

    def open(self, path, access=GENERIC_READ, flags=0):
        handle = self.k.CreateFileW(path, access, 3, None, 3, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            self.check(False, f"Could not open {path}")
        return handle

    def ioctl(self, handle, code, input_bytes=b"", output_size=65536):
        buffer = ctypes.create_string_buffer(output_size) if output_size else None
        data = ctypes.create_string_buffer(input_bytes) if input_bytes else None
        count = ctypes.c_uint32()
        self.check(
            self.k.DeviceIoControl(
                handle, code, data, len(input_bytes), buffer, output_size, ctypes.byref(count), None
            ),
            "Storage control failed",
        )
        return buffer.raw[: count.value] if buffer is not None else b""

    def close(self, handle):
        if handle is not None:
            self.k.CloseHandle(handle)


def _normal_serial(value):
    return "".join(value.split()).casefold()


def reject_target_files(target: UsbDevice, paths: list[Path]) -> None:
    """Do not erase the disk holding the ISO, helper, interpreter or status files."""
    api = WinApi()
    number = int(target.device_id.removeprefix("PhysicalDrive"))
    for path in paths:
        resolved = str(path.resolve())
        if resolved.startswith("\\\\"):
            raise WriteSafetyError("The writer requires local image and application files")
        root = ctypes.create_unicode_buffer(32768)
        volume = ctypes.create_unicode_buffer(1024)
        api.check(
            api.k.GetVolumePathNameW(resolved, root, len(root)),
            "Could not identify source filesystem",
        )
        api.check(
            api.k.GetVolumeNameForVolumeMountPointW(root.value, volume, len(volume)),
            "Could not identify source volume",
        )
        handle = api.open(volume.value.rstrip("\\"), access=0)
        try:
            extents = api.ioctl(handle, 0x00560000)  # IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS
            count = struct.unpack_from("<I", extents)[0]
            if not count or len(extents) < 8 + count * 24:
                raise WriteSafetyError("Source filesystem has unknown physical disk extents")
            if any(
                struct.unpack_from("<I", extents, 8 + i * 24)[0] == number for i in range(count)
            ):
                raise WriteSafetyError(
                    "The image or application is on the selected USB. Move it to an internal disk"
                )
        finally:
            api.close(handle)


@contextmanager
def locked_image(path: Path):
    """Hold the source open with writes/deletes denied until copying completes."""
    api = WinApi()
    handle = api.k.CreateFileW(str(path.resolve()), GENERIC_READ, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        api.check(False, "Cannot lock the ISO for reading")
    import msvcrt

    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        api.close(handle)
        raise
    with os.fdopen(fd, "rb", buffering=0) as source:
        yield source


class WindowsDisk:
    def __init__(self, target: UsbDevice):
        if not re.fullmatch(r"PhysicalDrive[1-9][0-9]*", target.device_id):
            raise WriteSafetyError("Invalid target disk")
        self.target = target
        self.api = WinApi()
        self.handle = None
        self.volumes = []
        self.alignment = 4096

    LOCK_TIMEOUT = 10.0
    REQUIRED_BUS_TYPE = 7  # BusTypeUsb

    def _bind_handle(self):
        length = self.api.ioctl(self.handle, 0x0007405C, output_size=8)
        device_number = self.api.ioctl(self.handle, 0x002D1080)
        descriptor = self.api.ioctl(self.handle, 0x002D1400, struct.pack("<III", 0, 0, 0))
        if len(length) != 8 or len(device_number) < 12 or len(descriptor) < 36:
            raise WriteSafetyError("USB driver returned incomplete disk identity information")
        size = struct.unpack("<q", length)[0]
        number = struct.unpack_from("<I", device_number, 4)[0]
        serial_offset = struct.unpack_from("<I", descriptor, 24)[0]
        bus = struct.unpack_from("<I", descriptor, 28)[0]
        if not 36 <= serial_offset < len(descriptor):
            raise WriteSafetyError("Native device serial is unavailable")
        serial = descriptor[serial_offset:].split(b"\0", 1)[0].decode("ascii", errors="replace")
        if (
            bus != self.REQUIRED_BUS_TYPE
            or number != int(self.target.device_id[13:])
            or size != self.target.size_bytes
            or _normal_serial(serial) != _normal_serial(self.target.serial)
        ):
            raise WriteSafetyError("The opened disk does not match the confirmed USB identity")
        logical, physical, sector_offset = self._alignment(size)
        if (
            logical != self.target.sector_size
            or physical < logical
            or physical > 65536
            or physical & (physical - 1)
            or physical % logical
            or sector_offset != 0
        ):
            raise WriteSafetyError("Unrecognised USB storage alignment")
        self.alignment = physical

    def _alignment(self, size):
        try:
            alignment = self.api.ioctl(self.handle, 0x002D1400, struct.pack("<III", 6, 0, 0))
        except OSError as exc:
            if exc.errno not in _ALIGNMENT_UNSUPPORTED:
                raise
            # Fall back to the logical sector size the disk class driver
            # reports. 4096-byte transfers are a multiple of every supported
            # logical size, so use them whenever the capacity allows; they are
            # valid for both 512-byte and 4096-byte physical media.
            geometry = self.api.ioctl(self.handle, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX)
            if len(geometry) < 32:
                raise WriteSafetyError("USB driver did not report its sector geometry") from exc
            logical = struct.unpack_from("<I", geometry, 20)[0]
            physical = 4096 if logical and 4096 % logical == 0 and size % 4096 == 0 else logical
            return logical, physical, 0
        if len(alignment) < 28:
            raise WriteSafetyError("USB driver did not report storage alignment")
        return struct.unpack_from("<III", alignment, 16)

    def _lock_volume(self, handle):
        # File Explorer, the search indexer or an antivirus scan often hold a
        # freshly inserted USB volume open for a few seconds. Retry the lock,
        # but never fall back to an unlocked or forced write.
        deadline = time.monotonic() + self.LOCK_TIMEOUT
        while True:
            try:
                self.api.ioctl(handle, FSCTL_LOCK_VOLUME, output_size=0)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise OSError(
                        exc.errno,
                        "Windows could not get exclusive access to the USB. Close File "
                        "Explorer windows and any files or apps using the USB, then try "
                        f"again. Nothing has been written. ({exc})",
                    ) from exc
            time.sleep(0.25)

    def __enter__(self):
        try:
            self.handle = self.api.open(
                "\\\\.\\" + self.target.device_id,
                GENERIC_READ | GENERIC_WRITE,
                0x80000000 | 0x20000000,
            )  # write-through, no OS cache
            self._bind_handle()
            # Fail before locking or dismounting volumes if the reported disk
            # length cannot be addressed with the native physical I/O unit.
            if self.target.size_bytes % self.alignment:
                raise WriteSafetyError(
                    "USB capacity is not aligned to its physical sector size"
                )
            current = next(
                (
                    d
                    for d in WindowsAdapter().list_usb_disks()
                    if d.device_id == self.target.device_id
                ),
                None,
            )
            if current is None:
                raise WriteSafetyError("USB disappeared before volume locking")
            validate_target(self.target, current, 1)
            number = int(self.target.device_id[13:])
            # Only the validated integer is interpolated. No caller shell text.
            # A blank, cleaned or previously failed USB can have no partitions;
            # Get-Partition reports that as ObjectNotFound. Any other error stops.
            script = (
                "$ErrorActionPreference='Stop'; $p=@(Get-Partition -DiskNumber "
                + str(number)
                + " -ErrorAction SilentlyContinue -ErrorVariable pe);"
                + " $bad=@($pe | Where-Object { [string]$_.CategoryInfo.Category -ne"
                + " 'ObjectNotFound' }); if ($bad.Count) { throw $bad[0] };"
                + " $v=@($p | ForEach-Object { Get-Volume -Partition $_"
                + " -ErrorAction SilentlyContinue } | Where-Object { $_.Path } |"
                + " Select-Object -ExpandProperty Path -Unique);"
                + " ConvertTo-Json -InputObject $v -Compress"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            volumes = json.loads(result.stdout or "[]")
            if not isinstance(volumes, list):
                raise WriteSafetyError("Unexpected volume discovery result")
            for path in volumes:
                if not re.fullmatch(r"\\\\\?\\Volume\{[0-9A-Fa-f-]{36}\}\\", path):
                    raise WriteSafetyError("Unexpected volume identifier")
                handle = self.api.open(path.rstrip("\\"), GENERIC_READ | GENERIC_WRITE)
                self.volumes.append(handle)
                extents = self.api.ioctl(handle, 0x00560000)
                count = struct.unpack_from("<I", extents)[0]
                if (
                    not count
                    or len(extents) < 8 + count * 24
                    or any(
                        struct.unpack_from("<I", extents, 8 + i * 24)[0] != number
                        for i in range(count)
                    )
                ):
                    raise WriteSafetyError("A USB volume changed disks before locking")
                self._lock_volume(handle)
                self.api.ioctl(handle, FSCTL_DISMOUNT_VOLUME, output_size=0)
            self._bind_handle()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def seek(self, offset, whence=0):
        if whence != 0 or offset < 0 or offset % self.alignment:
            raise ValueError("Raw disk offsets must be physically sector-aligned")
        position = ctypes.c_longlong()
        self.api.check(
            self.api.k.SetFilePointerEx(self.handle, offset, ctypes.byref(position), 0),
            "USB seek failed",
        )
        return position.value

    def _buffer(self, size):
        allocation = ctypes.create_string_buffer(size + self.alignment)
        address = (ctypes.addressof(allocation) + self.alignment - 1) & ~(self.alignment - 1)
        return allocation, address

    def write(self, data):
        if len(data) % self.alignment:
            raise ValueError("Raw write length must be physically sector-aligned")
        allocation, address = self._buffer(len(data))
        ctypes.memmove(address, data, len(data))
        count = ctypes.c_uint32()
        self.api.check(
            self.api.k.WriteFile(self.handle, address, len(data), ctypes.byref(count), None),
            "USB write failed",
        )
        if count.value != len(data):
            raise OSError("Short physical disk write")
        return count.value

    def read(self, size):
        if size <= 0 or size % self.alignment:
            raise ValueError("Raw read length must be physically sector-aligned")
        allocation, address = self._buffer(size)
        count = ctypes.c_uint32()
        self.api.check(
            self.api.k.ReadFile(self.handle, address, size, ctypes.byref(count), None),
            "USB read-back failed",
        )
        return ctypes.string_at(address, count.value)

    def flush(self):
        self.api.check(self.api.k.FlushFileBuffers(self.handle), "USB flush failed")

    def reopen_for_verify(self):
        self.api.close(self.handle)
        self.handle = None
        self.handle = self.api.open("\\\\.\\" + self.target.device_id, GENERIC_READ, 0x20000000)
        self._bind_handle()

    def __exit__(self, *_):
        if self.handle is not None:
            try:
                self.api.ioctl(self.handle, 0x00070140)  # IOCTL_DISK_UPDATE_PROPERTIES, best effort
            except OSError:
                pass
            self.api.close(self.handle)
            self.handle = None
        for handle in reversed(self.volumes):
            try:
                self.api.ioctl(handle, FSCTL_UNLOCK_VOLUME, output_size=0)
            except OSError:
                pass
            self.api.close(handle)
        self.volumes.clear()
