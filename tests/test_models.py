import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoboot_usb.catalogue import BY_KEY, CATALOGUE
from autoboot_usb.models import ImageSelection, UsbDevice, enough_capacity, format_size
from autoboot_usb.services.checksum import file_sha256
from autoboot_usb.services.writer import WriteNotAvailable, create_bootable_usb


class ModelTests(unittest.TestCase):
    def test_curated_releases_have_valid_pinned_sha256(self):
        self.assertEqual(len(CATALOGUE), 11)
        self.assertEqual(CATALOGUE[0].key, "kali")
        for release in [*BY_KEY["kali"].releases, *BY_KEY["ubuntu"].releases]:
            self.assertTrue(release.validated)
            if release.url:
                self.assertTrue(release.url.startswith("https://"))
                self.assertTrue(release.download_available)
            self.assertTrue(release.filename.endswith(".iso"))

    def test_internal_disk_never_offered(self):
        system_usb = UsbDevice("PhysicalDrive0", "OS disk", 30_000_000_000, "usb", True, True)
        internal_disk = UsbDevice("PhysicalDrive1", "Internal", 30_000_000_000, "sata", True, False)
        external_usb = UsbDevice(
            "PhysicalDrive2", "Portable USB", 30_000_000_000, "usb", True, False
        )
        read_only = UsbDevice(
            "PhysicalDrive3", "Locked", 30_000_000_000, "usb", True, False, read_only=True
        )
        self.assertFalse(system_usb.safe_to_offer)
        self.assertFalse(
            UsbDevice(
                "PhysicalDrive0", "USB disk zero", 30_000_000_000, "usb", True, False
            ).safe_to_offer
        )
        self.assertFalse(internal_disk.safe_to_offer)
        self.assertTrue(external_usb.safe_to_offer)
        self.assertFalse(read_only.safe_to_offer)

    def test_size_format(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(8_000_000_000), "8.0 GB")

    def test_image_verification_requires_trusted_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.iso"
            path.write_bytes(b"valid fake bytes")
            actual = file_sha256(path)
            self.assertEqual(len(actual), 64)
            local = ImageSelection(path, "imported", actual)
            self.assertFalse(local.verified)
            trusted = ImageSelection(path, "official", actual, actual)
            self.assertTrue(trusted.verified)
            self.assertTrue(
                enough_capacity(UsbDevice("test", "usb", 10_000, "usb", True, False), path)
            )
            self.assertFalse(enough_capacity(UsbDevice("test", "usb", 4, "usb", True, False), path))

    def test_usb_writer_requires_explicit_erasure_consent(self):
        # The USB writer is implemented; an unconfirmed call must *never*
        # proceed to discovery, volume locks or raw disk access.
        fake = UsbDevice("PhysicalDrive8", "Test", 16_000_000_000, "usb", True, False)
        with self.assertRaisesRegex(WriteNotAvailable, "Explicit confirmation"):
            create_bootable_usb(Path("test.iso"), fake)
        with patch("autoboot_usb.services.writer.sys.platform", "linux"):
            with self.assertRaisesRegex(WriteNotAvailable, "only on Windows"):
                create_bootable_usb(Path("test.iso"), fake, confirmed=True)

    def test_models_accept_large_images_and_reject_invalid_sizes(self):
        self.assertEqual(format_size(6_482_409_472), "6.5 GB")
        with self.assertRaises(ValueError):
            format_size(-1)
        usb = UsbDevice("PhysicalDrive8", "Test", 8_000_000_000, "usb", True, False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.iso"
            path.write_bytes(b"image")
            self.assertTrue(enough_capacity(usb, path))
            self.assertFalse(
                enough_capacity(UsbDevice("small", "Small", 2, "usb", True, False), path)
            )


if __name__ == "__main__":
    unittest.main()
