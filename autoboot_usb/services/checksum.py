"""Exact-name checksum parsing and cancellable, stable-file hashing."""

import hashlib
import os
import re
from pathlib import Path
from typing import BinaryIO, Callable


class OperationCancelled(InterruptedError):
    """Cancellation never implies a partially written USB is usable."""


def check_cancelled(cancel: Callable[[], bool] | None) -> None:
    if cancel and cancel():
        raise OperationCancelled("Operation cancelled")


def manifest_sha256(text: str, filename: str) -> str:
    matches = []
    for line in text.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})[ \t]+\*?(.+)", line.strip())
        if match and match[2] == filename:
            matches.append(match[1].lower())
        bsd = re.fullmatch(r"SHA256 \((.+)\) = ([a-fA-F0-9]{64})", line.strip())
        if bsd and bsd[1] == filename:
            matches.append(bsd[2].lower())
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one SHA-256 entry for {filename}")
    return matches[0]


def stream_sha256(
    stream: BinaryIO,
    size: int,
    callback: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> str:
    if size <= 0:
        raise ValueError("Image is empty")
    sha = hashlib.sha256()
    read = 0
    check_cancelled(cancel)
    if callback:
        callback(0, size)
    while read < size:
        check_cancelled(cancel)
        chunk = stream.read(min(4 * 1024**2, size - read))
        if not chunk:
            raise OSError("Image ended before its expected length")
        sha.update(chunk)
        read += len(chunk)
        if callback:
            callback(read, size)
    check_cancelled(cancel)
    return sha.hexdigest()


def file_sha256(
    path: Path,
    callback: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> str:
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        result = stream_sha256(stream, before.st_size, callback, cancel)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OSError("Image changed while its checksum was being calculated")
        if stream.read(1):
            raise OSError("Image grew while its checksum was being calculated")
        return result
