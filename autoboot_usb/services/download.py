"""HTTPS-only downloads, upstream manifest checks and no-clobber completion."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import ssl
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal

from .. import __version__
from ..models import Release
from .checksum import OperationCancelled, check_cancelled, file_sha256, manifest_sha256

APPROVED_ORIGINS = {
    "cdimage.kali.org",
    "releases.ubuntu.com",
    "cdimage.debian.org",
    "mirrors.edge.kernel.org",
    "download.fedoraproject.org",
    "archlinux.org",
    "geo.mirror.pkgbuild.com",
    "deb.parrot.sh",
    "www.microsoft.com",
    "software.download.prss.microsoft.com",
    "download.microsoft.com",
    "software-download.microsoft.com",
    "download.windowsupdate.com",
    "swscan.apple.com",
    "swcdn.apple.com",
    "swdist.apple.com",
    "updates.cdn-apple.com",
}
MIN_IMAGE_BYTES = 100 * 1024**2
RESERVE_BYTES = 150 * 1024**2
CancelledDownload = OperationCancelled


class VendorLinkRequired(ValueError):
    """A cached Microsoft image failed; a fresh signed download link is needed."""


class AppleChunkVerifier:
    def __init__(self, release):
        self.chunks = release.integrity_chunks
        if not self.chunks or sum(n for n, _ in self.chunks) != release.size_bytes:
            raise ValueError("Apple integrity data does not cover the installer")
        self.index = 0
        self.remaining = self.chunks[0][0]
        self.hash = hashlib.sha256()

    def update(self, data):
        offset = 0
        while offset < len(data):
            if self.index >= len(self.chunks):
                raise ValueError("Apple installer exceeds its integrity data")
            count = min(self.remaining, len(data) - offset)
            self.hash.update(data[offset : offset + count])
            offset += count
            self.remaining -= count
            if self.remaining == 0:
                if self.hash.hexdigest() != self.chunks[self.index][1]:
                    raise ValueError(f"Apple installer integrity failed at chunk {self.index + 1}")
                self.index += 1
                if self.index < len(self.chunks):
                    self.remaining = self.chunks[self.index][0]
                    self.hash = hashlib.sha256()

    def finish(self):
        if self.index != len(self.chunks):
            raise ValueError("Apple installer integrity data is incomplete")


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise ValueError("Official download redirected to an insecure connection")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def official_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in APPROVED_ORIGINS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Download origin is not an approved official website")


def https_opener():
    return urllib.request.build_opener(
        HttpsOnlyRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )


def _request(url: str, method: str = "GET"):
    return urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": f"AutoBootUSB/{__version__}",
            "Accept-Encoding": "identity",
            "Accept": "application/octet-stream,*/*",
        },
    )


def _check_response(response) -> None:
    if urllib.parse.urlsplit(response.geturl()).scheme != "https":
        raise ValueError("Remote server did not provide an HTTPS response")
    if response.status != 200:
        raise ValueError(f"Unexpected HTTP status: {response.status}")


def check_upstream_checksum(release: Release, opener=None) -> None:
    """The live manifest must agree with our pin. It never replaces the pin."""
    official_url(release.checksum_url)
    if release.image_type == "windows-iso":
        # Microsoft's HTML hashes are bundled for the exact English/x64 edition.
        # Its signed ISO URL expires; the byte hash does not. Browser resolution
        # avoids requiring the HTML page to accept a second non-browser request.
        if not release.validated:
            raise ValueError("The Windows release has no trusted checksum")
        return
    if release.image_type == "apple-installer":
        return  # The resolver has just validated Apple's software-update catalog.
    with (opener or https_opener()).open(_request(release.checksum_url), timeout=35) as response:
        _check_response(response)
        raw = response.read(1024**2 + 1)
    if len(raw) > 1024**2:
        raise ValueError("Upstream checksum manifest exceeds the size limit")
    if manifest_sha256(raw.decode("utf-8"), release.filename) != release.sha256:
        raise ValueError("Published SHA-256 differs from the bundled release. Download blocked.")


def download_image(
    release: Release,
    folder: Path,
    progress: Callable[[int, int, float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    *,
    repair_cache: bool = False,
) -> tuple[Path, str]:
    if not release.download_available:
        raise ValueError("This edition has no verified direct download. Use its official website.")
    if release.url:
        official_url(release.url)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(iso|pkg)", release.filename):
        raise ValueError("Unsafe release filename")
    check_cancelled(cancel)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / release.filename
    apple = release.image_type == "apple-installer"
    apple_chunks = apple and release.verification_algorithm == "sha256-chunks"
    expected = release.verification_digest if apple else release.sha256
    if apple_chunks and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Apple did not supply valid SHA-256 integrity metadata")
    if (
        apple
        and not apple_chunks
        and (
            release.verification_algorithm != "sha1" or not re.fullmatch(r"[0-9a-f]{40}", expected)
        )
    ):
        raise ValueError("Apple did not supply a valid package integrity digest")
    if dest.exists():
        digest = (
            ""
            if dest.stat().st_size == 0
            else file_sha256(
                dest, lambda n, total: progress(n, total, 0) if progress else None, cancel
            )
        )
        cached_digest = digest
        if apple:
            check = AppleChunkVerifier(release) if apple_chunks else hashlib.sha1()
            try:
                with dest.open("rb") as cached:
                    while block := cached.read(4 * 1024**2):
                        check_cancelled(cancel)
                        check.update(block)
                if apple_chunks:
                    check.finish()
                    cached_digest = expected
                else:
                    cached_digest = check.hexdigest()
            except ValueError:
                cached_digest = ""
        if cached_digest != expected:
            if not repair_cache:
                raise FileExistsError(
                    "A different file with this name already exists. Rename it and retry."
                )
            # Preserve the failed cache rather than repeatedly offering bad bytes.
            dest.rename(folder / f"{release.filename}.invalid-{time.time_ns()}")
        else:
            return dest, digest
    if release.provider == "microsoft" and not release.url:
        raise VendorLinkRequired("Getting a fresh Windows download link from Microsoft")
    official_url(release.url)
    if release.size_bytes and shutil.disk_usage(folder).free < release.size_bytes + RESERVE_BYTES:
        raise OSError("Not enough free space in the Images folder for this download")
    opener = https_opener()
    check_upstream_checksum(release, opener)
    check_cancelled(cancel)
    tmp = (
        folder / f".{release.filename}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.part"
    )
    try:
        with opener.open(_request(release.url), timeout=35) as response:
            _check_response(response)
            if response.headers.get("Content-Type", "").lower().startswith("text/"):
                raise ValueError("The server returned a web page instead of an ISO")
            header = response.headers.get("Content-Length")
            advertised = int(header) if header else 0
            if advertised < 0 or (advertised and advertised < MIN_IMAGE_BYTES):
                raise ValueError("The server returned an invalid image length")
            if release.size_bytes and advertised and release.size_bytes != advertised:
                raise ValueError("The server's image length differs from the pinned release")
            total = release.size_bytes or advertised
            if total and shutil.disk_usage(folder).free < total + RESERVE_BYTES:
                raise OSError("Not enough free space in the Images folder for this download")
            start = time.monotonic()
            last_progress = 0.0
            downloaded = 0
            hasher = hashlib.sha256()
            integrity = (
                AppleChunkVerifier(release)
                if apple_chunks
                else (hashlib.sha1() if apple else hasher)
            )
            with tmp.open("xb") as output:
                while True:
                    check_cancelled(cancel)
                    chunk = response.read(1024**2)
                    if not chunk:
                        break
                    output.write(chunk)
                    hasher.update(chunk)
                    if apple:
                        integrity.update(chunk)
                    downloaded += len(chunk)
                    if total and downloaded > total:
                        raise OSError("Download exceeds the expected release length")
                    now = time.monotonic()
                    if progress and now - last_progress >= 0.1:
                        progress(downloaded, total, downloaded / max(now - start, 0.001))
                        last_progress = now
                output.flush()
                os.fsync(output.fileno())
        check_cancelled(cancel)
        if total and downloaded != total:
            raise OSError(f"Incomplete download: expected {total} bytes, got {downloaded}")
        if downloaded < MIN_IMAGE_BYTES:
            raise ValueError("The downloaded file is too small to be an OS image")
        digest = hasher.hexdigest()
        if apple_chunks:
            integrity.finish()
        elif integrity.hexdigest() != expected:
            algorithm = "Apple package integrity" if apple else "SHA-256"
            raise ValueError(
                f"{algorithm} verification failed. No image was saved. Download again."
            )
        # Windows rename refuses to overwrite. POSIX hard-link creation also fails
        # atomically if another download has published the same destination.
        if os.name == "nt":
            os.rename(tmp, dest)
        else:
            os.link(tmp, dest)
            tmp.unlink()
        if progress:
            progress(downloaded, downloaded, 0)
        return dest, digest
    finally:
        tmp.unlink(missing_ok=True)


class ImageDownloader(QThread):
    # Python integers preserve multi-gigabyte byte counts across Qt signals.
    progress = Signal(object, object, float)
    completed = Signal(str, str)
    failed = Signal(str)
    cancelled = Signal()
    resolved = Signal(object)
    link_required = Signal()

    def __init__(self, release: Release, folder: Path, parent=None):
        super().__init__(parent)
        self.release = release
        self.folder = folder
        self.stop_event = threading.Event()

    def cancel(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            if self.release.provider == "apple" and not self.release.url:
                from .providers import resolve_apple_installer

                self.release = resolve_apple_installer(self.release, self.stop_event.is_set)
                self.resolved.emit(self.release)
            path, digest = download_image(
                self.release,
                self.folder,
                self.progress.emit,
                self.stop_event.is_set,
                repair_cache=True,
            )
            self.completed.emit(str(path), digest)
        except OperationCancelled:
            self.cancelled.emit()
        except VendorLinkRequired:
            self.link_required.emit()
        except Exception as exc:
            self.failed.emit(f"Download failed: {exc}")


class ImageVerifier(QThread):
    progress = Signal(object, object)
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.stop_event = threading.Event()

    def cancel(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            last = [0.0]

            def report(count, total):
                now = time.monotonic()
                if now - last[0] >= 0.1 or count == total:
                    self.progress.emit(count, total)
                    last[0] = now

            result = file_sha256(self.path, report, self.stop_event.is_set)
            self.completed.emit(result)
        except OperationCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"Could not verify image: {exc}")
