"""Standard FAT32 Windows installation media, with split WIM and file read-back."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..services.checksum import check_cancelled, stream_sha256
from ..services.writer import WriteSafetyError, validate_target
from .windows import WindowsAdapter
from .windows_tools import powershell, run_command, system_binary
from .windows_writer import (
    WinApi,
    WindowsDisk,
    locked_image,
    reject_target_files,
    volume_disk_numbers,
)

FAT32_MAX_FILE = 2**32 - 1
CHUNK = 4 * 1024**2


@contextmanager
def mounted_iso(image: Path, cancel):
    check_cancelled(cancel)
    result = json.loads(
        powershell(
            "$before=Get-DiskImage -ImagePath $r.image; $owned=-not $before.Attached; "
            "try {$mounted=Mount-DiskImage -ImagePath $r.image -Access ReadOnly -PassThru; "
            "$volumes=@($mounted | Get-Volume | Where-Object DriveLetter); "
            "if($volumes.Count -ne 1){throw 'Windows ISO has an ambiguous volume layout'}; "
            "@{root=([string]$volumes[0].DriveLetter+':\\');owned=$owned} | ConvertTo-Json -Compress"
            "} catch {if($owned){Dismount-DiskImage -ImagePath $r.image -ErrorAction SilentlyContinue};throw}",
            {"image": str(image)},
            cancellable=False,
        )
    )
    try:
        if not re.fullmatch(r"[A-Za-z]:\\", result.get("root", "")):
            raise WriteSafetyError("Windows returned an unexpected ISO mount path")
        check_cancelled(cancel)
        yield Path(result["root"])
    finally:
        if result.get("owned"):
            powershell(
                "Dismount-DiskImage -ImagePath $r.image", {"image": str(image)}, cancellable=False
            )


def windows_file_plan(source: Path) -> list[tuple[Path, Path]]:
    for relative in ("efi/boot/bootx64.efi", "boot/bcd", "sources/boot.wim", "setup.exe"):
        if not (source / relative).is_file():
            raise WriteSafetyError(f"Windows installer is missing {relative}")
    files = []
    for path in sorted(source.rglob("*")):
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise WriteSafetyError("Windows image contains an unexpected reparse point")
        if path.is_file():
            relative = path.relative_to(source)
            if (
                info.st_size > FAT32_MAX_FILE
                and relative.as_posix().lower() != "sources/install.wim"
            ):
                raise WriteSafetyError(f"{relative} is too large for the FAT32 boot partition")
            files.append((path, relative))
    if not any(
        relative.as_posix().lower() in {"sources/install.wim", "sources/install.esd"}
        for _, relative in files
    ):
        raise WriteSafetyError("Windows image contains no installation payload")
    return files


def split_windows_payload(plan, scratch: Path, cancel):
    output = []
    for source, relative in plan:
        if source.stat().st_size <= FAT32_MAX_FILE:
            output.append((source, relative))
            continue
        if relative.as_posix().lower() != "sources/install.wim":
            raise WriteSafetyError("Unsupported oversized Windows installation file")
        if shutil.disk_usage(scratch).free < source.stat().st_size + 512 * 1024**2:
            raise OSError("More free space is needed to prepare the Windows installation files")
        folder = scratch / "split-wim"
        folder.mkdir()
        run_command(
            [
                system_binary("dism.exe"),
                "/English",
                "/Split-Image",
                f"/ImageFile:{source}",
                f"/SWMFile:{folder / 'install.swm'}",
                f"/FileSize:{min(3800, max(1, FAT32_MAX_FILE // 1024**2 - 1))}",
                "/CheckIntegrity",
            ],
            cancel,
        )
        pieces = sorted(folder.glob("install*.swm"))
        if not pieces or not (folder / "install.swm").is_file():
            raise WriteSafetyError("Windows did not produce split installation files")
        for piece in pieces:
            if not 0 < piece.stat().st_size <= FAT32_MAX_FILE:
                raise WriteSafetyError("Invalid split Windows installation file")
            output.append((piece, Path("sources") / piece.name))
    return output


FORMAT_SCRIPT = r"""
function Check-Target {
 $d=Get-Disk -Number $r.number
 if($d.Number -eq 0 -or $d.IsBoot -or $d.IsSystem -or $d.IsReadOnly -or
    [string]$d.BusType -ne 'USB' -or [long]$d.Size -ne [long]$r.target.size_bytes -or
    ([string]$d.FriendlyName).Trim() -ne ([string]$r.target.name).Trim() -or
    ([string]$d.SerialNumber).Trim() -ne ([string]$r.target.serial).Trim() -or
    ([string]$d.UniqueId).Trim() -ne ([string]$r.target.unique_id).Trim()) {throw 'Confirmed USB identity changed'}
 return $d
}
$d=Check-Target
if($d.IsOffline){$d | Set-Disk -IsOffline $false}
$d=Check-Target
$d | Clear-Disk -RemoveData -RemoveOEM -Confirm:$false
$d=Check-Target
if([string]$d.PartitionStyle -eq 'RAW'){$d | Initialize-Disk -PartitionStyle MBR | Out-Null}
$d=Check-Target
if([string]$d.PartitionStyle -ne 'MBR'){throw 'Windows USB partition initialisation failed'}
$p=New-Partition -DiskNumber $d.Number -Size ([long]$r.partition_size) -MbrType 12 -IsActive -AssignDriveLetter
$null=Check-Target
$p | Format-Volume -FileSystem FAT32 -NewFileSystemLabel AUTOBOOT -Force -Confirm:$false | Out-Null
$null=Check-Target
$v=$p | Get-Volume
@{path=[string]$v.Path;letter=[string]$v.DriveLetter;size=[long]$v.Size} | ConvertTo-Json -Compress
"""


def format_windows_usb(target, needed: int):
    partition_size = min(30_000_000_000, target.size_bytes - 4 * 1024**2)
    partition_size = partition_size // (1024**2) * (1024**2)
    if partition_size < needed + 512 * 1024**2:
        raise WriteSafetyError("USB does not have enough FAT32 boot-partition capacity")
    payload = {
        "number": int(target.device_id.removeprefix("PhysicalDrive")),
        "target": vars(target),
        "partition_size": partition_size,
    }
    result = json.loads(powershell(FORMAT_SCRIPT, payload, cancellable=False))
    guid = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    if (
        not re.fullmatch(r"\\\\\?\\Volume\{" + guid + r"\}\\", result.get("path", ""))
        or not re.fullmatch(r"[A-Za-z]", result.get("letter", ""))
        or type(result.get("size")) is not int
        or not needed <= result["size"] <= partition_size
    ):
        raise WriteSafetyError("Windows returned an unexpected USB volume")
    return result


def check_usb_volume(target, volume_path):
    current = next(
        (d for d in WindowsAdapter().list_usb_disks() if d.device_id == target.device_id), None
    )
    if current is None:
        raise WriteSafetyError("USB disconnected during Windows media creation")
    validate_target(target, current, 1)
    api = WinApi()
    handle = api.open(volume_path.rstrip("\\"), access=0)
    try:
        if volume_disk_numbers(api, handle) != {int(target.device_id[13:])}:
            raise WriteSafetyError("USB volume no longer belongs to the confirmed drive")
    finally:
        api.close(handle)


def copy_windows_files(plan, destination: Path, progress=None, cancel=None):
    report = progress or (lambda *_: None)
    total = sum(source.stat().st_size for source, _ in plan)
    manifest, copied = [], 0
    report("writing", 0, total)
    for source, relative in plan:
        check_cancelled(cancel)
        if relative.is_absolute() or ".." in relative.parts:
            raise WriteSafetyError("Invalid Windows installation file path")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        for parent in (destination, *target.parents):
            if parent == destination.parent:
                break
            info = parent.lstat()
            if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise WriteSafetyError("USB directory was replaced by a reparse point")
        digest = hashlib.sha256()
        with source.open("rb") as src, target.open("xb") as output:
            while chunk := src.read(CHUNK):
                check_cancelled(cancel)
                if output.write(chunk) != len(chunk):
                    raise OSError("Incomplete Windows installation file write")
                digest.update(chunk)
                copied += len(chunk)
                report("writing", copied, total)
            output.flush()
            os.fsync(output.fileno())
        manifest.append((relative, source.stat().st_size, digest.hexdigest()))
    report("flushing", copied, total)
    return manifest, total


def verify_windows_files(manifest, destination: Path, total: int, progress=None, cancel=None):
    report = progress or (lambda *_: None)
    done = 0
    report("verifying", 0, total)
    for relative, size, expected in manifest:
        check_cancelled(cancel)
        with (destination / relative).open("rb") as stream:
            digest = (
                stream_sha256(
                    stream, size, lambda n, _: report("verifying", done + n, total), cancel
                )
                if size
                else hashlib.sha256(b"").hexdigest()
            )
            if digest != expected or stream.read(1):
                raise WriteSafetyError(f"USB file verification failed: {relative}")
        done += size
    check_cancelled(cancel)
    report("complete", total, total)


def create_windows_usb(image, target, release, progress=None, cancel=None):
    report = progress or (lambda *_: None)
    current = next(
        (d for d in WindowsAdapter().list_usb_disks() if d.device_id == target.device_id), None
    )
    if current is None:
        raise WriteSafetyError("Selected USB was disconnected")
    reject_target_files(target, [image, Path(__file__), Path(__import__("sys").executable)])
    with locked_image(image) as source:
        size = source.seek(0, 2)
        validate_target(target, current, size)
        source.seek(0)
        digest = stream_sha256(source, size, lambda n, t: report("checking", n, t), cancel)
        if digest != release.sha256:
            raise WriteSafetyError("Windows ISO checksum changed. Nothing has been written")
        report("preparing", 0, 0)
        with (
            mounted_iso(image, cancel) as mounted,
            tempfile.TemporaryDirectory(prefix="autoboot-windows-") as scratch,
        ):
            plan = split_windows_payload(windows_file_plan(mounted), Path(scratch), cancel)
            needed = sum(path.stat().st_size for path, _ in plan)
            with WindowsDisk(target):
                pass  # Identity and existing volume locks checked before erasure.
            check_cancelled(cancel)
            report("writing", 0, needed)
            volume = format_windows_usb(target, needed)
            destination = Path(volume["path"])
            check_usb_volume(target, volume["path"])
            manifest, total = copy_windows_files(plan, destination, report, cancel)
            bootsect = mounted / "boot" / "bootsect.exe"
            if bootsect.is_file():
                check_usb_volume(target, volume["path"])
                run_command(
                    [str(bootsect), "/nt60", volume["letter"] + ":", "/mbr"],
                    cancel,
                    cancellable=False,
                )
            check_usb_volume(target, volume["path"])
            verify_windows_files(manifest, destination, total, report, cancel)
    return digest
