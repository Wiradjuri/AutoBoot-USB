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

from ..models import Release
from .checksum import OperationCancelled, check_cancelled, file_sha256, manifest_sha256

APPROVED_ORIGINS = {"cdimage.kali.org", "releases.ubuntu.com"}
MIN_IMAGE_BYTES = 100 * 1024**2
RESERVE_BYTES = 150 * 1024**2
CancelledDownload = OperationCancelled


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
            "User-Agent": "AutoBootUSB/0.2.2",
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
) -> tuple[Path, str]:
    if not release.download_available:
        raise ValueError("This edition has no verified direct download. Use its official website.")
    official_url(release.url)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.iso", release.filename):
        raise ValueError("Unsafe release filename")
    check_cancelled(cancel)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / release.filename
    if dest.exists():
        digest = file_sha256(
            dest, lambda n, total: progress(n, total, 0) if progress else None, cancel
        )
        if digest != release.sha256:
            raise FileExistsError(
                "A different file with this name already exists. Rename it and retry."
            )
        return dest, digest
    if release.size_bytes and shutil.disk_usage(folder).free < release.size_bytes + RESERVE_BYTES:
        raise OSError("Not enough free space in Downloads for this ISO image")
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
                raise OSError("Not enough free space in Downloads for this ISO image")
            start = time.monotonic()
            last_progress = 0.0
            downloaded = 0
            hasher = hashlib.sha256()
            with tmp.open("xb") as output:
                while True:
                    check_cancelled(cancel)
                    chunk = response.read(1024**2)
                    if not chunk:
                        break
                    output.write(chunk)
                    hasher.update(chunk)
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
        if digest != release.sha256:
            raise ValueError("SHA-256 verification failed. No image was saved. Download again.")
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

    def __init__(self, release: Release, folder: Path, parent=None):
        super().__init__(parent)
        self.release = release
        self.folder = folder
        self.stop_event = threading.Event()

    def cancel(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            path, digest = download_image(
                self.release, self.folder, self.progress.emit, self.stop_event.is_set
            )
            self.completed.emit(str(path), digest)
        except OperationCancelled:
            self.cancelled.emit()
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
