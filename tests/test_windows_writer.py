"""Exercise WindowsDisk identity/locking lifecycle with a fake API, never Win32."""

import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autoboot_usb.adapters.windows_writer import (
    FSCTL_DISMOUNT_VOLUME,
    FSCTL_LOCK_VOLUME,
    IOCTL_DISK_GET_DRIVE_GEOMETRY,
    IOCTL_STORAGE_GET_DEVICE_NUMBER,
    IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
    StorageControlError,
    WindowsDisk,
    volume_disk_numbers,
)
from autoboot_usb.models import UsbDevice
from autoboot_usb.services.writer import WriteSafetyError

DEVICE = UsbDevice(
    "PhysicalDrive2",
    "Test USB",
    16_000_000_000,
    "usb",
    True,
    False,
    serial="TEST-0002",
    unique_id="USB-ID",
)
VOLUME = r"\\?\Volume{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}" + "\\"


class Api:
    def __init__(
        self,
        serial="TEST-0002",
        bus=7,
        disk_number=2,
        size=16_000_000_000,
        volume_disk=2,
        failed_lock=False,
        sector_offset=0,
        physical_sector=4096,
        unsupported_alignment=False,
        unsupported_extents=False,
        error_code=1,
        logical_sector=512,
    ):
        self.serial, self.bus, self.number, self.size = serial, bus, disk_number, size
        self.volume_disk, self.failed_lock = volume_disk, failed_lock
        self.sector_offset = sector_offset
        self.physical_sector = physical_sector
        self.unsupported_alignment = unsupported_alignment
        self.unsupported_extents = unsupported_extents
        self.error_code, self.logical_sector = error_code, logical_sector
        self.opened, self.closed, self.controls = [], [], []
        self.k = SimpleNamespace()

    def open(self, path, access=None, flags=0):
        self.opened.append((path, access, flags))
        return len(self.opened)

    def close(self, handle):
        self.closed.append(handle)

    def ioctl(self, handle, code, input_bytes=b"", output_size=65536):
        self.controls.append((handle, code))
        if code == 0x0007405C:
            return struct.pack("<q", self.size)
        if code == 0x002D1080:
            if self.opened[handle - 1][0] == VOLUME.rstrip("\\"):
                return struct.pack("<III", 7, self.volume_disk, 0xFFFFFFFF)
            return struct.pack("<III", 7, self.number, 0xFFFFFFFF)
        if code == IOCTL_DISK_GET_DRIVE_GEOMETRY:
            return struct.pack("<qIIII", 100, 11, 255, 63, self.logical_sector)
        if code == 0x002D1400:
            if struct.unpack_from("<I", input_bytes)[0] == 6:
                if self.unsupported_alignment:
                    raise StorageControlError(self.error_code, "Alignment query not supported")
                return struct.pack(
                    "<IIIIIII", 28, 28, 0, 0, 512, self.physical_sector, self.sector_offset
                )
            header = bytearray(36)
            struct.pack_into("<I", header, 24, 36)
            struct.pack_into("<I", header, 28, self.bus)
            return bytes(header) + self.serial.encode() + b"\x00"
        if code == 0x00560000:
            if self.unsupported_extents:
                raise StorageControlError(self.error_code, "Volume extents not supported")
            return struct.pack("<I4xI4xqq", 1, self.volume_disk, 0, self.size)
        if code == FSCTL_LOCK_VOLUME and self.failed_lock:
            raise OSError("USB files are open; lock denied")
        return b""


class NativeBoundaryTests(unittest.TestCase):
    def context(self, api):
        return (
            patch("autoboot_usb.adapters.windows_writer.WinApi", return_value=api),
            patch("autoboot_usb.adapters.windows_writer.WindowsAdapter"),
            patch(
                "autoboot_usb.adapters.windows_writer.system_binary", return_value="powershell.exe"
            ),
        )

    def enter(self, api):
        api_patch, adapter_patch, binary_patch = self.context(api)
        with (
            api_patch,
            adapter_patch as adapter,
            binary_patch,
            patch(
                "autoboot_usb.adapters.windows_writer.subprocess.run",
                return_value=SimpleNamespace(stdout=json.dumps([VOLUME])),
            ),
        ):
            adapter.return_value.list_usb_disks.return_value = [DEVICE]
            with WindowsDisk(DEVICE) as disk:
                self.assertEqual(disk.alignment, 4096)
                return disk

    def test_usb_identity_checked_before_lock_and_all_handles_closed(self):
        api = Api()
        self.enter(api)
        self.assertEqual(api.opened[0][0], r"\\.\PhysicalDrive2")
        codes = [code for _, code in api.controls]
        self.assertLess(codes.index(FSCTL_LOCK_VOLUME), codes.index(FSCTL_DISMOUNT_VOLUME))
        self.assertEqual(sorted(api.closed), [1, 2, 3])

    def test_wrong_native_serial_bus_number_size_refused_before_lock(self):
        for api in [
            Api(serial="SWAPPED"),
            Api(bus=17),
            Api(disk_number=3),
            Api(size=8_000_000_000),
        ]:
            with self.subTest(api=vars(api)), self.assertRaises(WriteSafetyError):
                self.enter(api)
            self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])
            self.assertEqual(api.closed, [1])

    def test_nonzero_physical_alignment_offset_refused_before_lock(self):
        api = Api(sector_offset=512)
        with self.assertRaises(WriteSafetyError):
            self.enter(api)
        self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])
        self.assertEqual(api.closed, [1])

    def test_unaligned_physical_capacity_refused_before_lock(self):
        # 16_000_000_000 bytes is divisible by 4 KiB but not by 64 KiB.
        api = Api(physical_sector=65536)
        with self.assertRaisesRegex(WriteSafetyError, "capacity is not aligned"):
            self.enter(api)
        self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])
        self.assertEqual(api.closed, [1, 2])

    def test_volume_on_other_disk_refused_before_lock(self):
        api = Api(volume_disk=3)
        with self.assertRaises(WriteSafetyError):
            self.enter(api)
        self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])
        self.assertEqual(sorted(api.closed), [1, 2, 3])

    def test_failed_volume_lock_is_not_bypassed(self):
        api = Api(failed_lock=True)
        with self.assertRaises(OSError):
            self.enter(api)
        self.assertNotIn(FSCTL_DISMOUNT_VOLUME, [code for _, code in api.controls])
        self.assertEqual(sorted(api.closed), [1, 2, 3])

    def test_unsupported_usb_alignment_uses_checked_buffered_io(self):
        for error in (1, 50, 87):
            api = Api(unsupported_alignment=True, error_code=error)
            api_patch, adapter_patch, binary_patch = self.context(api)
            with (
                api_patch,
                adapter_patch as adapter,
                binary_patch,
                patch(
                    "autoboot_usb.adapters.windows_writer.subprocess.run",
                    return_value=SimpleNamespace(stdout=json.dumps([VOLUME])),
                ),
            ):
                adapter.return_value.list_usb_disks.return_value = [DEVICE]
                with WindowsDisk(DEVICE) as disk:
                    self.assertEqual(disk.alignment, 512)
                    self.assertFalse(disk.uncached)
                    self.assertFalse(api.opened[0][2] & 0x20000000)
            self.assertEqual(sorted(api.closed), [1, 2])

    def test_access_denial_is_never_treated_as_unsupported_alignment(self):
        api = Api(unsupported_alignment=True, error_code=5)
        with self.assertRaises(StorageControlError):
            self.enter(api)
        self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])

    def test_unsupported_extents_use_native_single_disk_identity(self):
        api = Api(unsupported_extents=True)
        self.enter(api)
        codes = [code for _, code in api.controls]
        self.assertIn(IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, codes)
        self.assertIn(IOCTL_STORAGE_GET_DEVICE_NUMBER, codes)
        self.assertIn(FSCTL_LOCK_VOLUME, codes)

    def test_fallback_volume_on_another_disk_is_still_refused(self):
        api = Api(unsupported_extents=True, volume_disk=3)
        with self.assertRaises(WriteSafetyError):
            self.enter(api)
        self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])

    def test_invalid_fallback_logical_geometry_is_refused_before_lock(self):
        api = Api(unsupported_alignment=True, logical_sector=4096)
        with self.assertRaises(WriteSafetyError):
            self.enter(api)
        self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])

    def test_failed_fallback_mapping_does_not_skip_volume_validation(self):
        api = Api(unsupported_extents=True)
        handle = api.open(VOLUME.rstrip("\\"))
        with patch.object(api, "ioctl", side_effect=StorageControlError(1, "Not supported")):
            with self.assertRaises(StorageControlError):
                volume_disk_numbers(api, handle)
