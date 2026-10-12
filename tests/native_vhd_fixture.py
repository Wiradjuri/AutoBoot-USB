"""Test-only elevated helper for a Windows CI virtual disk. Never used by the app.

Runs the production run_writer.py entry point, elevated_writer, locked_image,
reject_target_files and WindowsDisk code in a real ShellExecuteEx child. Only
four facts are replaced, because a CI runner has no USB stick or desktop:
the target's discovery record and native bus type (a VHD is not USB), the
curated-release lookup (the synthetic image is not a Kali/Ubuntu ISO) and the
interactive erase dialog (auto-confirmed).
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_writer  # noqa: E402
from autoboot_usb.adapters import windows, windows_writer  # noqa: E402
from autoboot_usb.models import Release, UsbDevice  # noqa: E402
from autoboot_usb.services import elevated_writer, writer  # noqa: E402

config = json.loads(Path(os.environ["AUTOBOOT_VHD_FIXTURE"]).read_text(encoding="utf-8"))
device = UsbDevice(**config["device"])
release = Release(
    "CI virtual disk image",
    "1",
    "Test",
    "x86-64",
    "image.iso",
    "",
    config["sha256"],
    0,
    size_bytes=config["image_size"],
)


def trusted(digest):
    if digest != release.sha256:
        raise writer.WriteSafetyError("Unexpected test image")
    return release


windows.WindowsAdapter.list_usb_disks = lambda self: [device]
windows_writer.WindowsDisk.REQUIRED_BUS_TYPE = config["native_bus"]
writer.trusted_release = trusted
elevated_writer.trusted_release = trusted
elevated_writer.confirm_erase = lambda data, target: True

if __name__ == "__main__":
    raise SystemExit(run_writer.main())
