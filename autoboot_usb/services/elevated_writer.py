"""Narrow privileged helper entry point. Never starts the GUI or accepts commands."""

from __future__ import annotations

import ctypes
import hashlib
import json
import secrets
import sys
import tempfile
import time
import traceback
from pathlib import Path

from ..models import UsbDevice, format_size
from .checksum import OperationCancelled
from .writer import create_bootable_usb, trusted_release
from .writer_session import atomic_publish, read_message

# MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST.
# The final confirmation is created in a *different, elevated process*, so it
# cannot be owned by the unprivileged Qt window. Explicitly foreground it rather
# than risking a hidden modal dialog that leaves the main app waiting forever.
ERASE_CONFIRM_FLAGS = 0x00050134


def confirm_erase(data: dict, target: UsbDevice) -> bool:
    """Display the final, elevated, disk-specific erase confirmation."""
    message = (
        f"Erase {target.name}?\n\nDisk: {target.device_id}\n"
        f"Capacity: {format_size(target.size_bytes)}\nSerial: {target.serial}\n"
        f"Image: {Path(data['image']).name}\n\n"
        "All partitions and files on this USB will be lost.\n"
        "This Windows writer preview has not been tested on physical hardware.\n"
        "Use a spare USB with no important files. Continue?"
    )
    fn = ctypes.windll.user32.MessageBoxW
    fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    return fn(None, message, "AutoBoot USB — confirm USB erasure", ERASE_CONFIRM_FLAGS) == 6


def atomic_status(session: Path, payload: dict) -> None:
    atomic_publish(session, "status.json", payload)


def await_status_ack(session: Path, cancel, timeout: float = 15.0) -> None:
    """Prove the original GUI can read elevated output before any disk work."""
    nonce = secrets.token_hex(16)
    atomic_status(session, {"state": "admin_approved", "nonce": nonce, "count": 0, "total": 0})
    deadline = time.monotonic() + timeout
    while True:
        if cancel():
            raise OperationCancelled()
        if read_message(session, "channel_ack.json").get("nonce") == nonce:
            return
        if time.monotonic() >= deadline:
            raise PermissionError(
                "Administrator access was granted, but the app could not read the writer's "
                "status. USB creation stopped before any disk was opened"
            )
        time.sleep(0.05)


def read_request(path: Path, expected_digest: str) -> dict:
    # Check original paths, before resolve() follows symlinks/junctions.
    for candidate in (path, path.parent):
        attributes = getattr(candidate.lstat(), "st_file_attributes", 0)
        if candidate.is_symlink() or attributes & 0x400:
            raise ValueError("Writer request cannot use reparse points")
    session = path.resolve().parent
    if (
        session.parent != Path(tempfile.gettempdir()).resolve()
        or not session.name.startswith("autoboot-write-")
        or path.name != "request.json"
        or session.is_symlink()
        or path.is_symlink()
    ):
        raise ValueError("Invalid writer request location")
    if path.stat().st_size > 16384:
        raise ValueError("Writer request is too large")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError("Writer request changed after confirmation")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {
        "image",
        "image_size",
        "sha256",
        "target",
        "confirmed",
        "experimental",
    }:
        raise ValueError("Invalid writer request fields")
    if data["confirmed"] is not True or data["experimental"] is not True:
        raise ValueError("Erasure and preview acknowledgement are required")
    if not isinstance(data["image"], str) or not Path(data["image"]).is_absolute():
        raise ValueError("An absolute image path is required")
    if type(data["image_size"]) is not int or data["image_size"] <= 0:
        raise ValueError("Invalid image size")
    trusted_release(data["sha256"])
    if not isinstance(data["target"], dict):
        raise ValueError("Invalid target snapshot")
    strings = {"device_id", "name", "bus", "serial", "unique_id"}
    booleans = {"removable", "system_disk", "read_only"}
    integers = {"size_bytes", "sector_size"}
    if set(data["target"]) != strings | booleans | integers:
        raise ValueError("Invalid target fields")
    for keys, kind in ((strings, str), (booleans, bool), (integers, int)):
        if any(type(data["target"][key]) is not kind for key in keys):
            raise ValueError("Invalid target field types")
    target = UsbDevice(**data["target"])
    if not target.safe_to_offer or not target.serial:
        raise ValueError("Target is not an identifiable writable USB")
    return data


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform != "win32" or len(argv) != 2:
        return 2
    path = Path(argv[0])
    # The bootstrap validates the session and records any request/import error
    # before it could otherwise disappear in a windowless Python process.
    data = read_request(path, argv[1])
    session = path.resolve().parent
    started = False
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise PermissionError("Administrator approval is required for USB writing")

        def cancel():
            return (session / "cancel").exists()

        await_status_ack(session, cancel)
        from ..adapters.windows_writer import reject_target_files

        target = UsbDevice(**data["target"])
        reject_target_files(target, [session, Path(__file__)])
        if Path(data["image"]).stat().st_size != data["image_size"]:
            raise ValueError("Image size changed after confirmation")

        if cancel():
            raise OperationCancelled()
        # This final confirmation is displayed by the elevated helper itself.
        # It binds consent to the immutable request that the helper received.
        atomic_status(session, {"state": "confirming_usb", "count": 0, "total": 0})
        if not confirm_erase(data, target):
            raise OperationCancelled()
        # Stopping from the main GUI while the confirmation is open must not
        # begin a disk write if Yes is subsequently pressed.
        if cancel():
            raise OperationCancelled()
        atomic_status(session, {"state": "checking", "count": 0, "total": 0})
        last = [0.0, ""]

        def report(phase, count, total):
            nonlocal started
            started = started or phase in {"writing", "flushing", "verifying", "complete"}
            now = time.monotonic()
            if now - last[0] >= 0.1 or last[1] != phase or count == total:
                atomic_status(session, {"state": phase, "count": count, "total": total})
                last[:] = [now, phase]

        digest = create_bootable_usb(
            Path(data["image"]),
            target,
            confirmed=True,
            expected_sha256=data["sha256"],
            progress=report,
            cancel=cancel,
        )
        atomic_status(
            session,
            {
                "state": "complete",
                "sha256": digest,
                "target": target.device_id,
                "count": data["image_size"],
                "total": data["image_size"],
            },
        )
        return 0
    except Exception as exc:
        state = "cancelled" if isinstance(exc, OperationCancelled) else "failed"
        suffix = (
            " USB may contain an incomplete image. Create it again before booting."
            if started
            else " No image was written."
        )
        atomic_status(
            session,
            {
                "state": state,
                "message": (str(exc) or "Cancelled")[:1000] + suffix,
                "traceback": traceback.format_exc()[-1500:],
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
