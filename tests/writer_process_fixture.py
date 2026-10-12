"""Test-only subprocess writer. Opens ordinary files, never a USB device."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autoboot_usb.services.elevated_writer import atomic_status  # noqa: E402
from autoboot_usb.services.writer import copy_and_verify  # noqa: E402


def main():
    session = Path(sys.argv[1])
    corrupt = sys.argv[2] == "corrupt"
    data = json.loads((session / "request.json").read_text(encoding="utf-8"))
    disk_path = session / "simulated-disk.bin"
    last_phase = None

    def report(phase, count, total):
        nonlocal last_phase
        atomic_status(session, {"state": phase, "count": count, "total": total})
        # Slow a tiny fixture down enough to exercise the real 200 ms IPC poll
        # and queued Qt signals. The production copy engine does not sleep.
        time.sleep(0.25 if phase != last_phase else 0.1)
        last_phase = phase

    class FileDisk:
        def __init__(self):
            self.stream = disk_path.open("w+b", buffering=0)
            self.stream.truncate(data["target"]["size_bytes"])

        def seek(self, offset, whence=0):
            return self.stream.seek(offset, whence)

        def write(self, value):
            return self.stream.write(value)

        def read(self, count):
            return self.stream.read(count)

        def flush(self):
            self.stream.flush()
            os.fsync(self.stream.fileno())

        def reopen_for_verify(self):
            self.stream.close()
            self.stream = disk_path.open("r+b", buffering=0)
            if corrupt:
                self.stream.seek(0)
                self.stream.write(b"!")
                self.flush()

    disk = FileDisk()
    try:
        with Path(data["image"]).open("rb", buffering=0) as image:
            digest = copy_and_verify(
                image,
                disk,
                data["image_size"],
                data["sha256"],
                data["target"]["size_bytes"],
                progress=report,
                cancel=lambda: (session / "cancel").exists(),
            )
        atomic_status(
            session,
            {
                "state": "complete",
                "sha256": digest,
                "target": data["target"]["device_id"],
                "count": data["image_size"],
                "total": data["image_size"],
            },
        )
        return 0
    except Exception as exc:
        from autoboot_usb.services.checksum import OperationCancelled

        atomic_status(
            session,
            {
                "state": "cancelled" if isinstance(exc, OperationCancelled) else "failed",
                "message": str(exc),
            },
        )
        return 1
    finally:
        disk.stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
