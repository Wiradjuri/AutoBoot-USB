"""Fault-injected disk writing tests. Only BytesIO or temporary files are used."""

import hashlib
import io
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from autoboot_usb.models import UsbDevice
from autoboot_usb.services.checksum import OperationCancelled
from autoboot_usb.services.elevated_writer import read_request
from autoboot_usb.services.writer import (
    WriteSafetyError,
    copy_and_verify,
    trusted_release,
    validate_hybrid_header,
    validate_target,
)

DATA = b"checked-linux-image\x00" * 4000 + b"end"
DIGEST = hashlib.sha256(DATA).hexdigest()
CAPACITY = 2 * 1024**2
DEVICE = UsbDevice(
    "PhysicalDrive2",
    "Test USB",
    CAPACITY,
    "usb",
    True,
    False,
    serial="TEST-0002",
    unique_id="ID-0002",
)


class Disk(io.BytesIO):
    def __init__(self):
        super().__init__(b"x" * CAPACITY)
        self.flushed = False
        self.reopened = False

    def flush(self):
        self.flushed = True

    def reopen_for_verify(self):
        if not self.flushed:
            raise AssertionError("Read-back without flushing")
        self.reopened = True


class ShortWriter(Disk):
    def write(self, data):
        return super().write(data[:17])


class StoppedWriter(Disk):
    def write(self, data):
        return 0


class CorruptDisk(Disk):
    def reopen_for_verify(self):
        super().reopen_for_verify()
        self.seek(10)
        self.write(b"!")


class ShortReader(Disk):
    def read(self, size):
        return super().read(max(0, size - 1))


class PhysicalFourKDisk(Disk):
    """Fail any unaligned raw I/O, like a strict 512e/4Kn USB driver."""

    alignment = 4096

    def seek(self, offset, whence=0):
        if whence == 0 and offset % self.alignment:
            raise AssertionError("Unaligned 4K seek")
        return super().seek(offset, whence)

    def write(self, data):
        if len(data) % self.alignment:
            raise AssertionError("Unaligned 4K write")
        return super().write(data)

    def read(self, size=-1):
        if size % self.alignment:
            raise AssertionError("Unaligned 4K read")
        return super().read(size)


class FlushFailure(Disk):
    def flush(self):
        raise OSError("Flush failed")


class WriterTests(unittest.TestCase):
    def execute(self, disk=None, data=DATA, digest=DIGEST, **kw):
        disk = disk or Disk()
        return copy_and_verify(io.BytesIO(data), disk, len(data), digest, CAPACITY, **kw)

    def test_full_copy_padding_tail_cleanup_flush_and_readback(self):
        disk = Disk()
        phases = []
        digest = self.execute(disk, progress=lambda phase, *_: phases.append(phase))
        self.assertEqual(digest, DIGEST)
        self.assertTrue(disk.flushed and disk.reopened)
        self.assertEqual(disk.getvalue()[: len(DATA)], DATA)
        padded = (len(DATA) + 511) // 512 * 512
        self.assertEqual(disk.getvalue()[len(DATA) : padded], bytes(padded - len(DATA)))
        self.assertEqual(disk.getvalue()[-(1024**2) :], bytes(1024**2))
        self.assertLess(phases.index("flushing"), phases.index("verifying"))
        self.assertEqual(phases[-1], "complete")

    def test_file_backed_disk_is_flushed_and_verified(self):
        import os

        # NamedTemporaryFile uses a method-forwarding wrapper on every OS.
        # Assigning its methods directly as FileDisk class attributes would
        # turn them into descriptors and bind FileDisk as the seek() offset.
        for file_factory in (tempfile.TemporaryFile, tempfile.NamedTemporaryFile):
            with self.subTest(file_factory=file_factory.__name__):
                with file_factory("w+b", buffering=0) as stream:
                    stream.truncate(CAPACITY)

                    class FileDisk:
                        def read(self, size=-1):
                            return stream.read(size)

                        def write(self, data):
                            return stream.write(data)

                        def seek(self, offset, whence=0):
                            return stream.seek(offset, whence)

                        def flush(self):
                            stream.flush()
                            os.fsync(stream.fileno())

                    self.assertEqual(self.execute(FileDisk()), DIGEST)
                    stream.seek(0)
                    self.assertEqual(stream.read(len(DATA)), DATA)

    def test_four_k_sector_padding(self):
        disk = Disk()
        self.assertEqual(self.execute(disk, sector_size=4096), DIGEST)
        self.assertEqual(disk.getvalue()[: len(DATA)], DATA)

    def test_512e_physical_4k_writes_and_readbacks_are_aligned(self):
        # Ubuntu's published ISO is 2048 bytes short of a 4096-byte multiple.
        # The shared writer must pad *only* the final transfer, then read the
        # exact ISO bytes back for the SHA-256 comparison.
        image = b"A" * 4096 + b"B" * 2048
        disk = PhysicalFourKDisk()
        digest = hashlib.sha256(image).hexdigest()
        result = copy_and_verify(
            io.BytesIO(image), disk, len(image), digest, CAPACITY, sector_size=512
        )
        self.assertEqual(result, digest)
        self.assertEqual(disk.getvalue()[: len(image)], image)
        self.assertEqual(disk.getvalue()[len(image):8192], bytes(2048))
        self.assertTrue(disk.flushed and disk.reopened)

    def test_unaligned_physical_capacity_rejected_before_write(self):
        disk = PhysicalFourKDisk()
        initial = disk.getvalue()
        with self.assertRaisesRegex(WriteSafetyError, "capacity is not aligned"):
            copy_and_verify(io.BytesIO(DATA), disk, len(DATA), DIGEST, CAPACITY - 512)
        self.assertEqual(disk.getvalue(), initial)

    def test_invalid_physical_geometry_refused_without_writing(self):
        disk = Disk()
        disk.alignment = 768
        before = disk.getvalue()
        with self.assertRaisesRegex(WriteSafetyError, "unsupported physical"):
            self.execute(disk)
        self.assertEqual(disk.getvalue(), before)

    def test_partial_writes_are_completed(self):
        self.assertEqual(self.execute(ShortWriter()), DIGEST)

    def test_zero_write_flush_failure_short_read_and_corruption_fail(self):
        for disk in [StoppedWriter(), FlushFailure(), ShortReader(), CorruptDisk()]:
            phases = []
            with self.subTest(disk=type(disk).__name__), self.assertRaises((OSError, ValueError)):
                self.execute(disk, progress=lambda phase, *_: phases.append(phase))
            self.assertNotIn("complete", phases)

    def test_wrong_source_checksum_leaves_disk_untouched(self):
        disk = Disk()
        before = disk.getvalue()
        with self.assertRaises(WriteSafetyError):
            self.execute(disk, digest="0" * 64)
        self.assertEqual(disk.getvalue(), before)

    def test_small_or_unaligned_disk_rejected_before_writing(self):
        for capacity in [len(DATA), CAPACITY - 1]:
            disk = Disk()
            before = disk.getvalue()
            with self.assertRaises(WriteSafetyError):
                copy_and_verify(io.BytesIO(DATA), disk, len(DATA), DIGEST, capacity)
            self.assertEqual(disk.getvalue(), before)

    def test_cancel_before_writing_leaves_disk_untouched(self):
        disk = Disk()
        before = disk.getvalue()
        with self.assertRaises(OperationCancelled):
            self.execute(disk, cancel=lambda: True)
        self.assertEqual(disk.getvalue(), before)

    def test_cancel_on_write_transition_before_tail_cleanup(self):
        disk = Disk()
        before = disk.getvalue()
        stop = [False]

        def progress(phase, *_):
            stop[0] = phase == "writing"

        with self.assertRaises(OperationCancelled):
            self.execute(disk, progress=progress, cancel=lambda: stop[0])
        self.assertEqual(disk.getvalue(), before)

    def test_cancel_during_verify_does_not_report_success(self):
        phases = []
        stop = [False]

        def progress(phase, *_):
            phases.append(phase)
            stop[0] = phase == "verifying"

        with self.assertRaises(OperationCancelled):
            self.execute(progress=progress, cancel=lambda: stop[0])
        self.assertNotIn("complete", phases)

    def test_device_swap_and_unsafe_targets_blocked(self):
        for current in [
            replace(DEVICE, serial="OTHER"),
            replace(DEVICE, size_bytes=CAPACITY * 2),
            replace(DEVICE, unique_id="NEW"),
            replace(DEVICE, system_disk=True),
            replace(DEVICE, read_only=True),
            replace(DEVICE, bus="nvme"),
        ]:
            with self.assertRaises(WriteSafetyError):
                validate_target(DEVICE, current, len(DATA))
        for disk in [
            replace(DEVICE, serial=""),
            replace(DEVICE, device_id="PhysicalDrive0"),
            replace(DEVICE, device_id="PhysicalDrive2; erase"),
            replace(DEVICE, sector_size=1024),
        ]:
            with self.assertRaises(WriteSafetyError):
                validate_target(disk, disk, len(DATA))
        validate_target(DEVICE, DEVICE, len(DATA))

    def test_unknown_or_windows_image_not_accepted(self):
        with self.assertRaises(WriteSafetyError):
            trusted_release(DIGEST)
        with self.assertRaises(WriteSafetyError):
            trusted_release("0" * 64)

    def test_hybrid_header_validation(self):
        image = bytearray(40000)
        image[510:512] = b"\x55\xaa"
        image[446:450] = b"boot"
        image[32769:32774] = b"CD001"
        validate_hybrid_header(io.BytesIO(image))
        image[510] = 0
        with self.assertRaises(WriteSafetyError):
            validate_hybrid_header(io.BytesIO(image))

    def test_elevated_request_binding_rejects_tampering(self):
        import json

        from autoboot_usb.catalogue import BY_KEY

        with tempfile.TemporaryDirectory(prefix="autoboot-write-") as directory:
            request = Path(directory) / "request.json"
            data = {
                "image": str(request.parent / "image.iso"),
                "image_size": 100,
                "sha256": BY_KEY["kali"].releases[0].sha256,
                "target": asdict(DEVICE),
                "confirmed": True,
                "experimental": True,
            }
            raw = json.dumps(data).encode()
            request.write_bytes(raw)
            bound = hashlib.sha256(raw).hexdigest()
            self.assertEqual(read_request(request, bound), data)
            request.write_bytes(raw.replace(b"TEST-0002", b"NEW-SERIAL"))
            with self.assertRaisesRegex(ValueError, "changed"):
                read_request(request, bound)

    def test_no_confirmation_cannot_launch_destructive_path(self):
        from autoboot_usb.services.writer import WriteNotAvailable, create_bootable_usb

        with patch(
            "autoboot_usb.adapters.windows_writer.WinApi",
            side_effect=AssertionError("must not open"),
        ):
            with self.assertRaises(WriteNotAvailable):
                create_bootable_usb(Path("image.iso"), DEVICE)
