"""Real Win32 raw-disk writes against a temporary virtual disk (Windows CI only).

These tests attach a small VHD and run the production WindowsDisk, volume
lock/dismount, unbuffered aligned writes, flush, uncached reopen and full
read-back, plus the GUI worker -> ShellExecuteEx -> elevated helper pipeline.
They need administrator rights and create/attach virtual disks, so they run
only when AUTOBOOT_NATIVE_DISK_TEST=1 (set by the Windows CI job), never as
part of TEST_WINDOWS.bat on a user's PC. No physical disk is opened.
"""

import ctypes
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from vhd_identity import CI_SERIAL, usb_identity_ioctl

ROOT = Path(__file__).resolve().parents[1]
ENABLED = (
    os.name == "nt"
    and os.environ.get("AUTOBOOT_NATIVE_DISK_TEST") == "1"
    and bool(getattr(ctypes, "windll", None) and ctypes.windll.shell32.IsUserAnAdmin())
)
MIB = 1024**2


def synthetic_hybrid_image(size: int) -> bytes:
    data = bytearray(os.urandom(size))
    data[446:510] = bytes(64)
    data[446] = 0x80  # non-empty, but type-0 (unused) partition entry
    data[510:512] = b"\x55\xaa"
    data[32769:32774] = b"CD001"
    return bytes(data)


def powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"PowerShell failed: {result.stderr.strip()}\n{script}")
    return result.stdout.strip()


def diskpart(commands: list[str]) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as script:
        script.write("\n".join(commands) + "\n")
    try:
        result = subprocess.run(
            ["diskpart", "/s", script.name], capture_output=True, text=True, timeout=180
        )
    finally:
        os.unlink(script.name)
    if result.returncode:
        raise RuntimeError(f"diskpart failed ({result.returncode}): {result.stdout[-1500:]}")


class VirtualDisk:
    def __init__(self, folder: Path, formatted: bool):
        self.path = folder / ("ntfs.vhd" if formatted else "raw.vhd")
        commands = [
            f'create vdisk file="{self.path}" maximum=64 type=fixed',
            f'select vdisk file="{self.path}"',
            "attach vdisk",
        ]
        if formatted:
            commands += [
                "convert mbr",
                "create partition primary",
                "format fs=ntfs quick label=ABTEST",
                "assign",
            ]
        diskpart(commands)
        deadline = time.monotonic() + 30
        while True:
            try:
                self.number = int(
                    powershell(f"(Get-DiskImage -ImagePath '{self.path}' | Get-Disk).Number")
                )
                break
            except (RuntimeError, ValueError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
        info = json.loads(
            powershell(
                f"Get-Disk -Number {self.number} | Select-Object FriendlyName, Size, "
                "SerialNumber, UniqueId, LogicalSectorSize, PartitionStyle | ConvertTo-Json"
            )
        )
        self.native_bus, self.native_serial = self._native_identity()
        from autoboot_usb.models import UsbDevice

        self.device = UsbDevice(
            device_id=f"PhysicalDrive{self.number}",
            name=str(info["FriendlyName"] or "Virtual disk").strip(),
            size_bytes=int(info["Size"]),
            bus="usb",  # snapshot shape the GUI offers; native bus checked separately
            removable=True,
            system_disk=False,
            serial=CI_SERIAL,  # injected into the native descriptor by vhd_identity
            read_only=False,
            unique_id=str(info["UniqueId"] or "").strip(),
            sector_size=int(info["LogicalSectorSize"] or 512),
        )
        self.letter = ""
        if formatted:
            self.letter = powershell(
                f"(Get-Partition -DiskNumber {self.number} | Get-Volume).DriveLetter"
            ).strip()

    def _native_identity(self):
        import struct

        from autoboot_usb.adapters.windows_writer import GENERIC_READ, WinApi

        api = WinApi()
        handle = api.open(rf"\\.\PhysicalDrive{self.number}", GENERIC_READ)
        try:
            descriptor = api.ioctl(handle, 0x002D1400, struct.pack("<III", 0, 0, 0))
        finally:
            api.close(handle)
        offset = struct.unpack_from("<I", descriptor, 24)[0]
        bus = struct.unpack_from("<I", descriptor, 28)[0]
        serial = ""
        if 36 <= offset < len(descriptor):
            serial = descriptor[offset:].split(b"\0", 1)[0].decode("ascii", "replace").strip()
        return bus, serial

    def read(self, length: int) -> bytes:
        aligned = (length + 4095) // 4096 * 4096
        with open(rf"\\.\PhysicalDrive{self.number}", "rb", buffering=0) as raw:
            return raw.read(aligned)[:length]

    def detach(self) -> None:
        try:
            powershell(f"Dismount-DiskImage -ImagePath '{self.path}' | Out-Null")
        finally:
            self.path.unlink(missing_ok=True)


if ENABLED:
    from autoboot_usb.adapters.windows_writer import WinApi


@unittest.skipUnless(ENABLED, "Windows CI administrator virtual-disk test")
class NativeVirtualDiskTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="autoboot-vhd-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    def attach(self, formatted: bool) -> VirtualDisk:
        disk = VirtualDisk(self.folder, formatted)
        self.addCleanup(disk.detach)
        spoof = patch.object(WinApi, "ioctl", usb_identity_ioctl(WinApi.ioctl))
        spoof.start()
        self.addCleanup(spoof.stop)
        return disk

    def native_write(self, disk: VirtualDisk, image: bytes):
        from autoboot_usb.adapters import windows
        from autoboot_usb.adapters.windows_writer import WindowsDisk
        from autoboot_usb.services.writer import copy_and_verify

        phases = []
        with patch.object(windows.WindowsAdapter, "list_usb_disks", lambda _: [disk.device]):
            with WindowsDisk(disk.device) as raw:
                alignment = raw.alignment
                digest = copy_and_verify(
                    io.BytesIO(image),
                    raw,
                    len(image),
                    hashlib.sha256(image).hexdigest(),
                    disk.device.size_bytes,
                    disk.device.sector_size,
                    progress=lambda phase, *_: phases.append(phase),
                )
        return digest, phases, alignment

    def test_production_writer_locks_busy_volume_writes_and_reads_back(self):
        disk = self.attach(formatted=True)
        self.assertTrue(disk.letter, "Formatted VHD should have a mounted volume")
        # Hold a file open on the USB volume, like Explorer or an AV scan, so
        # the first lock attempts fail and the retry has to succeed.
        busy = open(f"{disk.letter}:\\busy.txt", "w")
        busy.write("open while AutoBoot prepares the disk")
        busy.flush()
        timer = threading.Timer(1.5, busy.close)
        timer.start()
        try:
            image = synthetic_hybrid_image(40 * MIB + 2048)  # not a 4 KiB multiple
            started = time.monotonic()
            digest, phases, alignment = self.native_write(disk, image)
        finally:
            timer.join()
            busy.close()
        self.assertEqual(digest, hashlib.sha256(image).hexdigest())
        self.assertGreaterEqual(time.monotonic() - started, 1.0, "lock should have waited")
        for phase in ("checking", "writing", "flushing", "verifying", "complete"):
            self.assertIn(phase, phases)
        self.assertEqual(disk.read(len(image)), image)
        print(
            f"\nVHD PhysicalDrive{disk.number}: bus={disk.native_bus} "
            f"sector={disk.device.sector_size} alignment={alignment}",
            flush=True,
        )

    def test_partitionless_disk_is_written_without_volume_errors(self):
        disk = self.attach(formatted=False)
        style = powershell(f"(Get-Disk -Number {disk.number}).PartitionStyle")
        self.assertEqual(style, "RAW")
        image = synthetic_hybrid_image(24 * MIB + 512)
        digest, phases, _ = self.native_write(disk, image)
        self.assertEqual(digest, hashlib.sha256(image).hexdigest())
        self.assertEqual(disk.read(len(image)), image)

    def test_gui_worker_runs_real_elevated_helper_end_to_end(self):
        from PySide6.QtCore import QCoreApplication

        from autoboot_usb.models import ImageSelection, Release
        from autoboot_usb.services import write_worker

        app = QCoreApplication.instance() or QCoreApplication([])
        disk = self.attach(formatted=True)
        image = synthetic_hybrid_image(48 * MIB + 2048)
        digest = hashlib.sha256(image).hexdigest()
        path = self.folder / "image.iso"
        path.write_bytes(image)
        config = self.folder / "fixture.json"
        config.write_text(
            json.dumps(
                {
                    "device": vars(disk.device),
                    "sha256": digest,
                    "image_size": len(image),
                }
            ),
            encoding="utf-8",
        )
        release = Release("CI", "1", "Test", "x86-64", "image.iso", "", digest, 0)
        real_command = write_worker.helper_command

        def fixture_command(session, request_digest):
            executable, args = real_command(session, request_digest)
            args[2] = str(ROOT / "tests" / "native_vhd_fixture.py")
            return executable, args

        worker = write_worker.UsbWriteWorker(
            ImageSelection(path, "official", digest, digest), disk.device
        )
        completed, failed, cancelled, progress = [], [], [], []
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)
        worker.cancelled.connect(cancelled.append)
        worker.progress.connect(lambda *args: progress.append(args))
        with (
            patch.dict(os.environ, {"AUTOBOOT_VHD_FIXTURE": str(config)}),
            patch.object(write_worker, "trusted_release", return_value=release),
            patch.object(write_worker, "helper_command", fixture_command),
        ):
            worker.start()
            deadline = time.monotonic() + 180
            while worker.isRunning() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            if worker.isRunning():
                worker.cancel()
                worker.wait(30000)
                self.fail("End-to-end write did not finish")
            app.processEvents()
        if worker.session:
            self.addCleanup(shutil.rmtree, worker.session, True)
        self.assertEqual(failed + cancelled, [], progress[-5:])
        self.assertEqual(completed, [digest])
        # The GUI samples status every 200 ms; on a small virtual disk the
        # helper passes through confirmation and checking between samples.
        phases = [phase for phase, *_ in progress]
        for phase in ("starting_helper", "awaiting_admin", "admin_approved"):
            self.assertIn(phase, phases)
        self.assertTrue({"writing", "verifying"} & set(phases), phases)
        self.assertTrue(all(0 <= c <= t for _, c, t in progress), progress)
        self.assertEqual(disk.read(len(image)), image)
        status = json.loads((worker.session / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "complete")
        self.assertEqual(status["target"], disk.device.device_id)


if __name__ == "__main__":
    unittest.main()
