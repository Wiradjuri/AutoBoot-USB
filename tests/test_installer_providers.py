"""Vendor metadata, trusted cache reuse and browser download addresses."""

import hashlib
import plistlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from test_download_pipeline import HASH, PAYLOAD, RELEASE, Opener

from autoboot_usb.catalogue import BY_KEY, CATALOGUE
from autoboot_usb.services import providers
from autoboot_usb.services.checksum import manifest_sha256
from autoboot_usb.services.download import VendorLinkRequired, download_image
from autoboot_usb.ui.microsoft_download import validated_iso_url


class ProviderTests(unittest.TestCase):
    def test_every_named_sidebar_system_has_an_automatic_download(self):
        for system in CATALOGUE:
            if system.key != "other":
                self.assertTrue(system.releases[0].download_available, system.key)

    def test_fedora_bsd_manifest_and_duplicate_entries(self):
        self.assertEqual(manifest_sha256(f"SHA256 (test.iso) = {HASH.upper()}", "test.iso"), HASH)
        with self.assertRaises(ValueError):
            manifest_sha256(f"SHA256 (test.iso) = {HASH}\n{HASH} *test.iso", "test.iso")

    def test_microsoft_links_reject_credentials_insecure_urls_and_other_hosts(self):
        good = "https://software.download.prss.microsoft.com/dbazure/test_x64.iso?t=signed"
        self.assertEqual(validated_iso_url(good), good)
        for bad in (
            good.replace("https:", "http:"),
            good.replace(".iso?", ".exe?"),
            good.replace("prss.microsoft.com", "prss.microsoft.com.evil.test"),
            good.replace("https://", "https://user:pass@"),
        ):
            with self.assertRaises(ValueError):
                validated_iso_url(bad)

    def test_apple_catalog_resolves_full_stable_installer_and_vendor_digest(self):
        digest = hashlib.sha1(PAYLOAD).hexdigest()
        products = {}
        for index, (version, title, value) in enumerate(
            (
                ("26.1", "Install macOS 26", bytes.fromhex(digest)),
                ("27.0.1", "Install macOS 27", digest),
                ("28.0", "Install macOS 28 Beta", digest),
            )
        ):
            products[str(index)] = {
                "PostDate": datetime(2026, 10, 10),
                "Packages": [
                    {
                        "URL": f"http://swcdn.apple.com/{index}/InstallAssistant.pkg",
                        "Digest": value,
                        "Size": 12_000_000_000,
                    }
                ],
                "Distributions": {"English": f"https://swdist.apple.com/{index}/installer.dist"},
            }
        catalog = plistlib.dumps({"Products": products})

        def fetch(url, *_):
            if url.endswith(".sucatalog"):
                return catalog
            index = int(url.split("/")[-2])
            version, title, _ = (
                ("26.1", "Install macOS 26", digest),
                ("27.0.1", "Install macOS 27", digest),
                ("28.0", "Install macOS 28 Beta", digest),
            )[index]
            return f"<title>{title}</title><key>VERSION</key><string>{version}</string>".encode()

        with patch.object(providers, "_get", side_effect=fetch):
            release = providers.resolve_apple_installer(BY_KEY["macos"].releases[0])
        self.assertEqual(release.version, "27.0.1")
        self.assertEqual(release.verification_digest, digest)
        self.assertEqual(release.url, "https://swcdn.apple.com/1/InstallAssistant.pkg")
        self.assertEqual(release.filename, "macOS-27.0.1-InstallAssistant.pkg")

    def test_cached_windows_image_does_not_need_an_expiring_link(self):
        release = replace(RELEASE, provider="microsoft", image_type="windows-iso", url="")
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / release.filename).write_bytes(PAYLOAD)
            with patch(
                "autoboot_usb.services.download.https_opener",
                side_effect=AssertionError("No network"),
            ):
                self.assertEqual(download_image(release, Path(folder))[1], HASH)

    def test_corrupt_managed_cache_is_preserved_and_downloaded_again(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / RELEASE.filename).write_bytes(b"damaged image")
            with (
                patch("autoboot_usb.services.download.https_opener", return_value=Opener()),
                patch("autoboot_usb.services.download.MIN_IMAGE_BYTES", 32),
            ):
                path, digest = download_image(RELEASE, root, repair_cache=True)
            self.assertEqual(digest, HASH)
            self.assertEqual(path.read_bytes(), PAYLOAD)
            self.assertEqual(next(root.glob("*.invalid-*")).read_bytes(), b"damaged image")

    def test_corrupt_windows_cache_requests_a_fresh_vendor_link(self):
        release = replace(RELEASE, provider="microsoft", image_type="windows-iso", url="")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / release.filename).write_bytes(b"corrupt")
            with self.assertRaises(VendorLinkRequired):
                download_image(release, root, repair_cache=True)
            self.assertFalse((root / release.filename).exists())
            self.assertEqual(next(root.glob("*.invalid-*")).read_bytes(), b"corrupt")

    def test_apple_download_and_cache_use_catalog_integrity_not_a_self_pin(self):
        release = replace(
            RELEASE,
            provider="apple",
            image_type="apple-installer",
            filename="InstallAssistant.pkg",
            url="https://swcdn.apple.com/InstallAssistant.pkg",
            checksum_url=providers.APPLE_CATALOGS[0],
            sha256="",
            verification_algorithm="sha1",
            verification_digest=hashlib.sha1(PAYLOAD).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch("autoboot_usb.services.download.https_opener", return_value=Opener()),
                patch("autoboot_usb.services.download.MIN_IMAGE_BYTES", 32),
            ):
                path, digest = download_image(release, Path(folder))
            self.assertEqual(digest, HASH)
            with patch(
                "autoboot_usb.services.download.https_opener",
                side_effect=AssertionError("No network"),
            ):
                self.assertEqual(download_image(release, Path(folder)), (path, HASH))
            with self.assertRaises(FileExistsError):
                download_image(replace(release, verification_digest="0" * 40), Path(folder))
