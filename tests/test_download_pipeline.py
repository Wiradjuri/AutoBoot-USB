"""Download failures must never publish corrupt images or overwrite user files."""

import hashlib
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from autoboot_usb.models import Release
from autoboot_usb.services.checksum import OperationCancelled, file_sha256, manifest_sha256
from autoboot_usb.services.download import HttpsOnlyRedirect, download_image

PAYLOAD = b"autoboot-test-image\x00" * 6000
HASH = hashlib.sha256(PAYLOAD).hexdigest()
RELEASE = Release(
    "Test",
    "1",
    "Live",
    "x86-64",
    "test.iso",
    "https://cdimage.kali.org/test.iso",
    HASH,
    8_000_000_000,
    checksum_url="https://cdimage.kali.org/SHA256SUMS",
    size_bytes=len(PAYLOAD),
)


class Response(io.BytesIO):
    def __init__(
        self, data, *, url="https://official-mirror.example/test.iso", status=200, headers=None
    ):
        super().__init__(data)
        self.url, self.status = url, status
        self.headers = {} if headers is None else headers

    def geturl(self):
        return self.url


class Opener:
    def __init__(
        self, data=PAYLOAD, manifest=None, headers=None, status=200, url=None, before_image=None
    ):
        self.data, self.headers, self.status = data, headers, status
        self.manifest = (HASH + " *test.iso\n") if manifest is None else manifest
        self.url = url or "https://official-mirror.example/test.iso"
        self.before_image = before_image
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        if request.full_url.endswith("SHA256SUMS"):
            return Response(self.manifest.encode())
        if self.before_image:
            self.before_image()
        return Response(
            self.data,
            url=self.url,
            status=self.status,
            headers={"Content-Length": str(len(self.data))}
            if self.headers is None
            else self.headers,
        )


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.minimum = patch("autoboot_usb.services.download.MIN_IMAGE_BYTES", 32)
        self.minimum.start()

    def tearDown(self):
        self.minimum.stop()
        self.temp.cleanup()

    def run_download(self, opener=None, release=RELEASE, **kwargs):
        with patch("autoboot_usb.services.download.https_opener", return_value=opener or Opener()):
            return download_image(release, self.folder, **kwargs)

    def assert_clean(self):
        self.assertFalse((self.folder / "test.iso").exists())
        self.assertEqual(list(self.folder.glob("*.part")), [])
        self.assertEqual(list(self.folder.glob(".*.part")), [])

    def test_full_download_exact_hash_progress_and_atomic_finish(self):
        seen = []
        path, digest = self.run_download(progress=lambda *args: seen.append(args))
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(digest, HASH)
        self.assertEqual(seen[-1][:2], (len(PAYLOAD), len(PAYLOAD)))
        self.assertFalse(list(self.folder.glob(".*.part")))

    def test_cached_good_image_is_rehashed_without_network(self):
        (self.folder / "test.iso").write_bytes(PAYLOAD)
        with patch(
            "autoboot_usb.services.download.https_opener",
            side_effect=AssertionError("No network expected"),
        ):
            self.assertEqual(download_image(RELEASE, self.folder)[1], HASH)

    def test_cached_image_cancellation(self):
        (self.folder / "test.iso").write_bytes(PAYLOAD)
        with self.assertRaises(OperationCancelled):
            self.run_download(cancel=lambda: True)
        self.assertEqual((self.folder / "test.iso").read_bytes(), PAYLOAD)

    def test_existing_bad_file_is_never_overwritten(self):
        path = self.folder / "test.iso"
        path.write_bytes(b"user-owned-file")
        with self.assertRaises(FileExistsError):
            self.run_download()
        self.assertEqual(path.read_bytes(), b"user-owned-file")

    def test_manifest_mismatch_missing_or_duplicate_entry(self):
        for manifest in [
            "0" * 64 + " *test.iso\n",
            HASH + " *test.iso.other\n",
            (HASH + " *test.iso\n") * 2,
            "<html>404</html>",
        ]:
            with self.subTest(manifest=manifest):
                with self.assertRaises(ValueError):
                    self.run_download(Opener(manifest=manifest))
                self.assert_clean()

    def test_bad_download_hash_discards_partial(self):
        wrong = b"!" + PAYLOAD[1:]
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.run_download(Opener(data=wrong))
        self.assert_clean()

    def test_incomplete_download_discards_partial(self):
        with self.assertRaisesRegex(OSError, "Incomplete"):
            self.run_download(
                Opener(data=PAYLOAD[:-1], headers={"Content-Length": str(len(PAYLOAD))})
            )
        self.assert_clean()

    def test_wrong_published_length_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "length"):
            self.run_download(Opener(headers={"Content-Length": str(len(PAYLOAD) + 100)}))
        self.assert_clean()

    def test_unknown_header_length_still_uses_pin(self):
        self.assertEqual(self.run_download(Opener(headers={}))[1], HASH)

    def test_html_status_and_https_downgrade_blocked(self):
        for opener in [
            Opener(headers={"Content-Type": "text/html"}),
            Opener(status=206),
            Opener(url="http://mirror.example/test.iso"),
        ]:
            with self.assertRaises(ValueError):
                self.run_download(opener)
            self.assert_clean()

    def test_http_redirect_handler_rejects_downgrade(self):
        with self.assertRaises(ValueError):
            HttpsOnlyRedirect().redirect_request(
                None, None, 302, "", {}, "http://example.test/file"
            )

    def test_unapproved_origin_and_path_traversal_blocked(self):
        for release in [
            replace(RELEASE, url="https://evil.example/test.iso"),
            replace(RELEASE, url="http://cdimage.kali.org/test.iso"),
            replace(RELEASE, filename="../test.iso"),
            replace(RELEASE, url="https://user:secret@cdimage.kali.org/test.iso"),
        ]:
            with self.assertRaises(ValueError):
                self.run_download(release=release)
            self.assert_clean()

    def test_mid_download_cancellation_leaves_no_image(self):
        stopped = [False]
        with self.assertRaises(OperationCancelled):
            self.run_download(
                progress=lambda *_: stopped.__setitem__(0, True), cancel=lambda: stopped[0]
            )
        self.assert_clean()

    def test_insufficient_space_prevents_network(self):
        from collections import namedtuple

        Usage = namedtuple("Usage", "total used free")
        with patch("autoboot_usb.services.download.shutil.disk_usage", return_value=Usage(1, 1, 0)):
            with self.assertRaisesRegex(OSError, "space"):
                self.run_download()
        self.assert_clean()

    def test_destination_race_does_not_replace_another_file(self):
        path = self.folder / "test.iso"
        with self.assertRaises(FileExistsError):
            self.run_download(Opener(before_image=lambda: path.write_bytes(b"other-download")))
        self.assertEqual(path.read_bytes(), b"other-download")
        self.assertFalse(list(self.folder.glob(".*.part")))

    def test_network_error_never_publishes_file(self):
        with patch("autoboot_usb.services.download.https_opener") as create:
            create.return_value.open.side_effect = HTTPError(
                RELEASE.url, 404, "Not Found", {}, None
            )
            with self.assertRaises(HTTPError):
                download_image(RELEASE, self.folder)
        self.assert_clean()


class ChecksumTests(unittest.TestCase):
    def test_exact_filename_and_uppercase_digest(self):
        self.assertEqual(manifest_sha256(HASH.upper() + " *test.iso\n", "test.iso"), HASH)
        self.assertEqual(manifest_sha256(HASH + "  test.iso\n", "test.iso"), HASH)

    def test_mutating_file_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.iso"
            path.write_bytes(PAYLOAD)

            def change(*_):
                with path.open("ab") as stream:
                    stream.write(b"changed")

            with self.assertRaises(OSError):
                file_sha256(path, change)

    def test_empty_file_is_not_an_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.iso"
            path.touch()
            with self.assertRaises(ValueError):
                file_sha256(path)
