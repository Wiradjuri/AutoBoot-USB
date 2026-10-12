"""Elevated test child: private status channels only, no USB/device access."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_windows_ipc import TokenApi  # noqa: E402

from autoboot_usb.services.elevated_writer import await_status_ack  # noqa: E402
from autoboot_usb.services.writer_session import publish_message  # noqa: E402


def main():
    session = Path(sys.argv[1])
    level = TokenApi().process_integrity()
    await_status_ack(session, lambda: False)
    for count in range(300):
        publish_message(session, "status.jsonl", {"state": "writing", "count": count})
        time.sleep(0.002)
    publish_message(session, "status.jsonl", {"state": "complete", "integrity": level})


if __name__ == "__main__":
    main()
