"""Exercise WindowsDisk identity/locking lifecycle with a fake API, never Win32."""

import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autoboot_usb.adapters.windows_writer import (
    FSCTL_DISMOUNT_VOLUME,
    FSCTL_LOCK_VOLUME,
    WindowsDisk,
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
    ):
        self.serial, self.bus, self.number, self.size = serial, bus, disk_number, size
        self.volume_disk, self.failed_lock = volume_disk, failed_lock
        self.sector_offset = sector_offset
        self.physical_sector = physical_sector
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
            return struct.pack("<III", 7, self.number, 0xFFFFFFFF)
        if code == 0x002D1400:
            if struct.unpack_from("<I", input_bytes)[0] == 6:
                return struct.pack(
                    "<IIIIIII", 28, 28, 0, 0, 512, self.physical_sector, self.sector_offset
                )
            header = bytearray(36)
            struct.pack_into("<I", header, 24, 36)
            struct.pack_into("<I", header, 28, self.bus)
            return bytes(header) + self.serial.encode() + b"\x00"
        if code == 0x00560000:
            return struct.pack("<I4xI4xqq", 1, self.volume_disk, 0, self.size)
        if code == FSCTL_LOCK_VOLUME and self.failed_lock:
            raise OSError("USB files are open; lock denied")
        return b""


class NativeBoundaryTests(unittest.TestCase):
    def context(self, api):
        return (
            patch("autoboot_usb.adapters.windows_writer.WinApi", return_value=api),
            patch("autoboot_usb.adapters.windows_writer.WindowsAdapter"),
        )

    def enter(self, api):
        api_patch, adapter_patch = self.context(api)
        with (
            api_patch,
            adapter_patch as adapter,
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
        self.assertEqual(sorted(api.closed), [1, 2])

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
        self.assertEqual(api.closed, [1])

    def test_volume_on_other_disk_refused_before_lock(self):
        api = Api(volume_disk=3)
        with self.assertRaises(WriteSafetyError):
            self.enter(api)
        self.assertNotIn(FSCTL_LOCK_VOLUME, [code for _, code in api.controls])
        self.assertEqual(sorted(api.closed), [1, 2])

    def test_failed_volume_lock_is_not_bypassed(self):
        api = Api(failed_lock=True)
        with self.assertRaises(OSError):
            self.enter(api)
        self.assertNotIn(FSCTL_DISMOUNT_VOLUME, [code for _, code in api.controls])
        self.assertEqual(sorted(api.closed), [1, 2])
