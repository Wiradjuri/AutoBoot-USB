"""Shared image-copy engine. Physical access is confined to the elevated helper."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import BinaryIO, Callable

from ..catalogue import CATALOGUE
from ..models import Release, UsbDevice
from .checksum import check_cancelled, stream_sha256

CHUNK_BYTES = 4 * 1024**2


class WriteNotAvailable(RuntimeError):
    pass


class WriteSafetyError(ValueError):
    pass


def trusted_release(digest: str) -> Release:
    for system in CATALOGUE:
        for release in system.releases:
            if (
                release.validated
                and release.image_type == "hybrid-iso"
                and release.sha256 == digest
            ):
                return release
    raise WriteSafetyError(
        "Only checksum-verified, curated Kali/Ubuntu images can be written in this preview"
    )


def validate_target(expected: UsbDevice, current: UsbDevice, image_size: int) -> None:
    if not expected.safe_to_offer or not current.safe_to_offer:
        raise WriteSafetyError("The target is not a writable, non-system USB disk")
    if not re.fullmatch(r"PhysicalDrive[1-9][0-9]*", current.device_id):
        raise WriteSafetyError("Invalid physical disk identifier; disk zero is always protected")
    if not expected.serial or not current.serial:
        raise WriteSafetyError(
            "This drive exposes no serial number. Its identity cannot be safely checked"
        )
    if expected != current:
        raise WriteSafetyError(
            "USB identity or properties changed. Refresh drives and confirm the target again"
        )
    sector = current.sector_size
    if sector not in (512, 4096):
        raise WriteSafetyError("This preview supports only 512-byte or 4096-byte logical sectors")
    padded = (image_size + sector - 1) // sector * sector
    if image_size <= 0 or current.size_bytes < padded or current.size_bytes % sector:
        raise WriteSafetyError("USB capacity is insufficient or its geometry is invalid")


def validate_hybrid_header(source: BinaryIO) -> None:
    source.seek(0)
    mbr = source.read(512)
    if len(mbr) != 512 or mbr[510:512] != b"\x55\xaa" or not any(mbr[446:510]):
        raise WriteSafetyError("The image does not contain the expected hybrid disk boot header")
    source.seek(16 * 2048 + 1)
    if source.read(5) != b"CD001":
        raise WriteSafetyError("The image does not contain the expected ISO9660 header")
    source.seek(0)


def _transfer_unit(target: BinaryIO, sector_size: int) -> int:
    """Use physical-sector alignment for uncached I/O when the disk reports it.

    512e media can expose 512-byte logical sectors and 4096-byte physical sectors.
    Windows' unbuffered I/O requires an aligned buffer and may reject unaligned
    transfers. Refuse invalid or unfamiliar geometry before touching the disk.
    """
    physical = getattr(target, "alignment", sector_size)
    if (
        type(physical) is not int
        or physical < sector_size
        or physical > 65536
        or physical & (physical - 1)
        or physical % sector_size
    ):
        raise WriteSafetyError("The USB reports an unsupported physical sector alignment")
    return physical


def _write_all(target: BinaryIO, data: bytes) -> None:
    done = 0
    while done < len(data):
        count = target.write(data[done:])
        if not isinstance(count, int) or count <= 0 or count > len(data) - done:
            raise OSError("USB write stopped before all bytes were written")
        done += count


def copy_and_verify(
    source: BinaryIO,
    target: BinaryIO,
    size: int,
    expected_sha256: str,
    capacity: int,
    sector_size: int = 512,
    progress: Callable[[str, int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> str:
    """Prehash, aligned copy, flush, reopen (if supported), full read-back hash.

    This function never opens a disk. Tests pass ordinary temporary files.
    A successful digest confirms the copied bytes, not hardware boot compatibility.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise WriteSafetyError("A valid expected SHA-256 is required")
    if sector_size not in (512, 4096):
        raise WriteSafetyError("Invalid disk logical sector geometry")
    transfer_unit = _transfer_unit(target, sector_size)
    if capacity % transfer_unit:
        raise WriteSafetyError("Disk capacity is not aligned to its physical sector size")
    padded_size = (size + transfer_unit - 1) // transfer_unit * transfer_unit
    if size <= 0 or padded_size > capacity:
        raise WriteSafetyError("USB is too small for the image")
    report = progress or (lambda *_: None)
    source.seek(0)
    digest = stream_sha256(source, size, lambda n, t: report("checking", n, t), cancel)
    if digest != expected_sha256 or source.read(1):
        raise WriteSafetyError("Image checksum/length changed. Nothing has been written")
    check_cancelled(cancel)
    report("writing", 0, size)
    check_cancelled(cancel)
    # Remove an old backup GPT at the end without touching any image bytes.
    tail_start = max(padded_size, (max(0, capacity - 1024**2) // transfer_unit) * transfer_unit)
    if tail_start < capacity:
        target.seek(tail_start)
        _write_all(target, bytes(capacity - tail_start))
    source.seek(0)
    target.seek(0)
    written = 0
    copied_hash = hashlib.sha256()
    while written < size:
        check_cancelled(cancel)
        chunk = source.read(min(CHUNK_BYTES, size - written))
        if not chunk:
            raise OSError("Source image became unreadable during writing")
        copied_hash.update(chunk)
        payload = chunk + bytes((-len(chunk)) % transfer_unit)
        _write_all(target, payload)
        written += len(chunk)
        report("writing", written, size)
    if copied_hash.hexdigest() != expected_sha256 or source.read(1):
        raise WriteSafetyError("Source image changed during writing. USB must be recreated")
    check_cancelled(cancel)
    report("flushing", written, size)
    target.flush()
    reopen = getattr(target, "reopen_for_verify", None)
    if reopen:
        reopen()
    target.seek(0)
    report("verifying", 0, size)
    sha = hashlib.sha256()
    read = 0
    while read < size:
        check_cancelled(cancel)
        wanted = min(CHUNK_BYTES, size - read)
        aligned = (wanted + transfer_unit - 1) // transfer_unit * transfer_unit
        chunk = target.read(aligned)
        if len(chunk) != aligned:
            raise OSError("USB read-back ended early. USB has not been verified")
        sha.update(chunk[:wanted])
        read += wanted
        report("verifying", read, size)
    check_cancelled(cancel)
    if sha.hexdigest() != expected_sha256:
        raise WriteSafetyError(
            "USB read-back checksum mismatch. Recreate it; try a different USB if it repeats"
        )
    report("complete", size, size)
    return sha.hexdigest()


def create_bootable_usb(
    image: Path,
    target: UsbDevice,
    *,
    confirmed: bool = False,
    expected_sha256: str = "",
    progress=None,
    cancel=None,
) -> str:
    if confirmed is not True:
        raise WriteNotAvailable("Explicit confirmation of USB erasure is required")
    if sys.platform != "win32":
        raise WriteNotAvailable("Physical USB writing is currently supported only on Windows")
    release = trusted_release(expected_sha256)
    from ..adapters.windows import WindowsAdapter
    from ..adapters.windows_writer import WindowsDisk, locked_image, reject_target_files

    current = next(
        (d for d in WindowsAdapter().list_usb_disks() if d.device_id == target.device_id), None
    )
    if current is None:
        raise WriteSafetyError("Selected USB was disconnected or is no longer eligible")
    with locked_image(image) as source:
        source.seek(0, 2)
        size = source.tell()
        source.seek(0)
        if release.size_bytes and size != release.size_bytes:
            raise WriteSafetyError("Image length does not match the curated release")
        validate_target(target, current, size)
        validate_hybrid_header(source)
        reject_target_files(target, [image, Path(__file__), Path(__import__("sys").executable)])
        # Validate the source before locking/dismounting any USB volumes.
        prehash = stream_sha256(
            source, size, lambda n, t: progress("checking", n, t) if progress else None, cancel
        )
        if prehash != expected_sha256:
            raise WriteSafetyError("Image checksum changed. Nothing has been written")
        check_cancelled(cancel)
        if progress:
            progress("preparing", 0, 0)
        with WindowsDisk(target) as disk:
            return copy_and_verify(
                source,
                disk,
                size,
                expected_sha256,
                target.size_bytes,
                target.sector_size,
                progress,
                cancel,
            )
