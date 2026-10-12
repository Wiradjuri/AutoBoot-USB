"""Real child-process/file I/O and QThread signals; UAC/Win32 access is mocked."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from autoboot_usb.models import ImageSelection, UsbDevice
from autoboot_usb.services.write_worker import UsbWriteWorker

ROOT = Path(__file__).resolve().parents[1]


class Function:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class ProcessKernel:
    def __init__(self):
        self.process = None
        self.closed = []
        self.WaitForSingleObject = Function(self.wait)
        self.GetExitCodeProcess = Function(self.exit_code)
        self.CloseHandle = Function(self.closed.append)

    def wait(self, handle, milliseconds):
        try:
            self.process.wait(timeout=milliseconds / 1000)
        except subprocess.TimeoutExpired:
            return 258
        return 0

    def exit_code(self, handle, pointer):
        pointer._obj.value = self.process.returncode
        return True


class ProcessFlowTests(unittest.TestCase):
    def execute(self, mode="normal", cancel_write=False):
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "image.iso"
            content = b"test-file-image\x00" * (600_000) + b"unaligned-end"
            image.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            target = UsbDevice(
                "PhysicalDrive2",
                "Test USB",
                16 * 1024**2,
                "usb",
                True,
                False,
                serial="TEST-2",
            )
            worker = UsbWriteWorker(ImageSelection(image, "official", digest, digest), target)
            kernel = ProcessKernel()
            complete, failed, cancelled, progress = [], [], [], []
            worker.completed.connect(complete.append)
            worker.failed.connect(failed.append)
            worker.cancelled.connect(cancelled.append)

            def on_progress(phase, count, total):
                progress.append((phase, count, total))
                if cancel_write and phase == "writing" and count:
                    worker.cancel()

            worker.progress.connect(on_progress)

            def simulate_elevation(*_):
                # This fixture opens only a normal file in the test session.
                kernel.process = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-S",
                        str(ROOT / "tests" / "writer_process_fixture.py"),
                        str(worker.session),
                        mode,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return 123

            try:
                with (
                    patch("autoboot_usb.services.write_worker.sys.platform", "win32"),
                    patch("autoboot_usb.services.write_worker.trusted_release"),
                    patch("autoboot_usb.services.write_worker.check_helper_startup"),
                    patch(
                        "autoboot_usb.services.write_worker.launch_elevated",
                        side_effect=simulate_elevation,
                    ),
                    patch(
                        "autoboot_usb.services.write_worker.process_api",
                        return_value=kernel,
                    ),
                    patch("autoboot_usb.services.write_worker.LOG.exception"),
                ):
                    worker.start()
                    deadline = time.monotonic() + 15
                    while worker.isRunning() and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.005)
                    if worker.isRunning():
                        worker.cancel()
                        if kernel.process:
                            kernel.process.kill()
                        worker.wait(5000)
                        self.fail("Simulated write did not finish")
                    app.processEvents()
                self.assertEqual(kernel.closed, [123])
                if complete:
                    with (worker.session / "simulated-disk.bin").open("rb") as disk:
                        self.assertEqual(disk.read(len(content)), content)
                return digest, complete, failed, cancelled, progress
            finally:
                if kernel.process:
                    kernel.process.wait(timeout=5)
                if worker.session:
                    shutil.rmtree(worker.session)

    def test_real_process_copy_flush_reopen_readback_and_queued_progress(self):
        digest, complete, failed, cancelled, progress = self.execute()
        self.assertEqual(complete, [digest])
        self.assertFalse(failed or cancelled)
        phases = [phase for phase, *_ in progress]
        for phase in ("checking", "writing", "flushing", "verifying"):
            self.assertIn(phase, phases)
        self.assertLess(phases.index("writing"), phases.index("verifying"))
        self.assertTrue(
            any(phase == "writing" and 0 < count < total for phase, count, total in progress)
        )
        # The complete flag is emitted only after process exit and validation.
        self.assertNotIn("complete", phases)

    def test_real_process_corruption_is_failure_never_verified(self):
        _, complete, failed, cancelled, _ = self.execute("corrupt")
        self.assertFalse(complete or cancelled)
        self.assertIn("read-back checksum mismatch", failed[0])

    def test_real_process_cancellation_during_write_never_completes(self):
        _, complete, failed, cancelled, _ = self.execute(cancel_write=True)
        self.assertFalse(complete or failed)
        self.assertTrue(cancelled)
