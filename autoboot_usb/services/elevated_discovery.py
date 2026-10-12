"""Read-only administrator discovery. This entry point cannot write a disk."""

import ctypes
import hashlib
import json
from dataclasses import asdict

from ..adapters.windows import WindowsAdapter
from .elevated_writer import await_status_ack
from .writer_session import publish_message


def read_discovery_request(path, digest):
    raw = path.read_bytes()
    if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("USB discovery request changed")
    if json.loads(raw) != {"operation": "discover"}:
        raise ValueError("Invalid USB discovery request")


def discover_main(session):
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError("Administrator access is required for this USB scan")

    def cancel():
        return (session / "cancel").exists()

    await_status_ack(session, cancel)
    devices = WindowsAdapter().list_usb_disks(cancel)
    if len(devices) > 32:
        raise ValueError("Too many USB disks for one discovery response")
    publish_message(
        session, "status.jsonl", {"state": "complete", "devices": [asdict(d) for d in devices]}
    )
    return 0
