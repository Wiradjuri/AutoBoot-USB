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
from ..services.checksum import check_cancelled
from ..services.writer import WriteSafetyError, validate_target
from .windows import WindowsAdapter
from .windows_tools import system_binary

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_DISMOUNT_VOLUME = 0x00090020
FSCTL_UNLOCK_VOLUME = 0x0009001C
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
IOCTL_DISK_GET_DRIVE_GEOMETRY = 0x00070000
UNSUPPORTED_CONTROLS = {1, 50, 87}  # INVALID_FUNCTION, NOT_SUPPORTED, INVALID_PARAMETER


class StorageControlError(OSError):
    def __init__(self, code: int, message: str):
        super().__init__(code, message)
        self.winerror = code


def _unsupported(error: OSError) -> bool:
    return getattr(error, "winerror", None) in UNSUPPORTED_CONTROLS


def volume_disk_numbers(api, handle) -> set[int]:
    """Map a native volume without requiring every USB bridge to support extents."""
    try:
        extents = api.ioctl(handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS)
    except OSError as exc:
        if not _unsupported(exc):
            raise
        # A single-device volume may expose device numbering even when its
        # filesystem driver rejects volume extents. Spanned/ambiguous volumes
        # that cannot supply a valid disk number are still refused.
        data = api.ioctl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER)
        if len(data) < 12:
            raise WriteSafetyError("Volume returned an incomplete native disk identity")
        kind, number, _partition = struct.unpack_from("<III", data)
        if kind != 7 or number == 0xFFFFFFFF:
            raise WriteSafetyError("Volume cannot be mapped to an identifiable disk")
        return {number}
    if len(extents) < 8:
        raise WriteSafetyError("Volume returned incomplete physical disk extents")
    count = struct.unpack_from("<I", extents)[0]
    if not count or len(extents) < 8 + count * 24:
        raise WriteSafetyError("Volume has unknown physical disk extents")
    return {struct.unpack_from("<I", extents, 8 + i * 24)[0] for i in range(count)}


class WinApi:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Physical USB writing is implemented only on Windows")
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
            raise StorageControlError(
                error, f"{action} (Windows error {error}): {ctypes.FormatError(error).strip()}"
            )

    def open(self, path, access=GENERIC_READ, flags=0):
        handle = self.k.CreateFileW(path, access, 3, None, 3, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            self.check(False, f"Could not open {path}")
        return handle

    def ioctl(self, handle, code, input_bytes=b"", output_size=65536):
        buffer = ctypes.create_string_buffer(output_size) if output_size else None
        data = ctypes.create_string_buffer(input_bytes) if input_bytes else None
        count = ctypes.c_uint32()
        names = {
            IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS: "IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS",
            IOCTL_STORAGE_GET_DEVICE_NUMBER: "IOCTL_STORAGE_GET_DEVICE_NUMBER",
            IOCTL_STORAGE_QUERY_PROPERTY: "IOCTL_STORAGE_QUERY_PROPERTY",
            IOCTL_DISK_GET_DRIVE_GEOMETRY: "IOCTL_DISK_GET_DRIVE_GEOMETRY",
            FSCTL_LOCK_VOLUME: "FSCTL_LOCK_VOLUME",
            FSCTL_DISMOUNT_VOLUME: "FSCTL_DISMOUNT_VOLUME",
        }
        operation = names.get(code, f"storage control 0x{code:08X}")
        if code == IOCTL_STORAGE_QUERY_PROPERTY and len(input_bytes) >= 4:
            operation += f" (property {struct.unpack_from('<I', input_bytes)[0]})"
        self.check(
            self.k.DeviceIoControl(
                handle, code, data, len(input_bytes), buffer, output_size, ctypes.byref(count), None
            ),
            operation,
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
            if number in volume_disk_numbers(api, handle):
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
    def __init__(self, target: UsbDevice, cancel=None):
        if not re.fullmatch(r"PhysicalDrive[1-9][0-9]*", target.device_id):
            raise WriteSafetyError("Invalid target disk")
        self.target = target
        self.cancel = cancel
        self.api = WinApi()
        self.handle = None
        self.volumes = []
        self.alignment = 4096
        self.uncached = True
        self.opened_uncached = False

    def _lock_volume(self, handle):
        deadline = time.monotonic() + 10.0
        while True:
            check_cancelled(self.cancel)
            try:
                self.api.ioctl(handle, FSCTL_LOCK_VOLUME, output_size=0)
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33}:
                    raise
                if time.monotonic() >= deadline:
                    raise OSError(
                        "Windows could not lock the USB. Close files and apps using it, then retry."
                    ) from exc
                time.sleep(0.2)

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
            bus != 7
            or number != int(self.target.device_id[13:])
            or size != self.target.size_bytes
            or _normal_serial(serial) != _normal_serial(self.target.serial)
        ):
            raise WriteSafetyError("The opened disk does not match the confirmed USB identity")
        try:
            alignment = self.api.ioctl(self.handle, 0x002D1400, struct.pack("<III", 6, 0, 0))
        except OSError as exc:
            if not _unsupported(exc):
                raise
            if self.opened_uncached:
                raise WriteSafetyError(
                    "USB alignment information changed on the open disk handle"
                ) from exc
            # Some removable-storage drivers don't expose physical alignment.
            # Verify their logical geometry, then use write-through buffered
            # I/O. Never guess a physical sector for unbuffered writes.
            geometry = self.api.ioctl(self.handle, IOCTL_DISK_GET_DRIVE_GEOMETRY)
            if len(geometry) < 24:
                raise WriteSafetyError("USB returned incomplete logical disk geometry")
            logical = struct.unpack_from("<I", geometry, 20)[0]
            if logical not in {512, 4096} or logical != self.target.sector_size:
                raise WriteSafetyError("USB logical geometry changed or is unsupported")
            self.alignment = logical
            self.uncached = False
            return
        if len(alignment) < 28:
            raise WriteSafetyError("USB driver did not report storage alignment")
        logical, physical, sector_offset = struct.unpack_from("<III", alignment, 16)
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

    def __enter__(self):
        try:
            self.handle = self.api.open(
                "\\\\.\\" + self.target.device_id,
                GENERIC_READ | GENERIC_WRITE,
                0x80000000,
            )  # write-through; query geometry before selecting uncached I/O
            self.opened_uncached = False
            self._bind_handle()
            if self.uncached:
                self.api.close(self.handle)
                self.handle = None
                self.handle = self.api.open(
                    "\\\\.\\" + self.target.device_id,
                    GENERIC_READ | GENERIC_WRITE,
                    0x80000000 | 0x20000000,
                )
                self.opened_uncached = True
                self._bind_handle()
                if not self.uncached:
                    raise WriteSafetyError("USB alignment information changed before writing")
            # Fail before locking or dismounting volumes if the reported disk
            # length cannot be addressed with the native physical I/O unit.
            if self.target.size_bytes % self.alignment:
                raise WriteSafetyError("USB capacity is not aligned to its physical sector size")
            current = next(
                (
                    d
                    for d in WindowsAdapter().list_usb_disks(cancel=self.cancel)
                    if d.device_id == self.target.device_id
                ),
                None,
            )
            if current is None:
                raise WriteSafetyError("USB disappeared before volume locking")
            validate_target(self.target, current, 1)
            number = int(self.target.device_id[13:])
            # Only the validated integer is interpolated. No caller shell text.
            script = (
                "$ErrorActionPreference='Stop'; $p=@(Get-Partition -DiskNumber "
                + str(number)
                + " -ErrorAction SilentlyContinue -ErrorVariable pe);"
                + " $bad=@($pe | Where-Object { [string]$_.CategoryInfo.Category -ne 'ObjectNotFound' });"
                + " if($bad.Count){throw $bad[0]}; $v=@($p | ForEach-Object { "
                + " $volume=Get-Volume -Partition $_ -ErrorAction SilentlyContinue -ErrorVariable ve;"
                + " $bad=@($ve | Where-Object { [string]$_.CategoryInfo.Category -ne 'ObjectNotFound' });"
                + " if($bad.Count){throw $bad[0]}; $volume }"
                + " | Where-Object { $_.Path } | Select-Object -ExpandProperty Path -Unique);"
                + " ConvertTo-Json -InputObject $v -Compress"
            )
            result = subprocess.run(
                [
                    system_binary("WindowsPowerShell/v1.0/powershell.exe"),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
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
                if volume_disk_numbers(self.api, handle) != {number}:
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
        self.handle = self.api.open(
            "\\\\.\\" + self.target.device_id,
            GENERIC_READ,
            0x20000000 if self.uncached else 0,
        )
        self.opened_uncached = self.uncached
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
