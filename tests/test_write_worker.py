"""Mocked Windows helper lifecycle: no process elevation or disk I/O occurs."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from autoboot_usb.catalogue import BY_KEY
from autoboot_usb.models import ImageSelection, UsbDevice
from autoboot_usb.services.write_worker import (
    UsbWriteWorker,
    check_helper_startup,
    helper_command,
    launch_elevated,
)


class Function:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class Kernel:
    def __init__(self, exit_code=0):
        self.closed = []
        waits = iter([258, 258, 0])
        self.WaitForSingleObject = Function(lambda *_: next(waits))

        def exit_value(handle, pointer):
            pointer._obj.value = exit_code
            return True

        self.GetExitCodeProcess = Function(exit_value)
        self.CloseHandle = Function(lambda handle: self.closed.append(handle))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "image.iso"
        self.path.write_bytes(b"GUI-only-fixture")
        self.digest = BY_KEY["kali"].releases[0].sha256
        self.image = ImageSelection(self.path, "official", self.digest, self.digest)
        self.target = UsbDevice(
            "PhysicalDrive2", "Test USB", 16_000_000_000, "usb", True, False, serial="TEST-2"
        )

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, result, exit_code=0, elevation_error=None, startup_error=None):
        import shutil

        worker = UsbWriteWorker(self.image, self.target)
        completed, failed, cancelled, progress = [], [], [], []
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)
        worker.cancelled.connect(cancelled.append)
        worker.progress.connect(lambda *args: progress.append(args))
        kernel = Kernel(exit_code)
        intermediate = {"state": "checking", "count": 5_368_709_120, "total": 6_442_450_944}
        waiting = {"state": "confirming_usb", "count": 0, "total": 0}
        with (
            patch("autoboot_usb.services.write_worker.sys.platform", "win32"),
            patch(
                "autoboot_usb.services.write_worker.check_helper_startup", side_effect=startup_error
            ),
            patch(
                "autoboot_usb.services.write_worker.launch_elevated",
                side_effect=elevation_error,
                return_value=123,
            ),
            patch("autoboot_usb.services.write_worker.process_api", return_value=kernel),
            patch(
                "autoboot_usb.services.write_worker._read_status",
                side_effect=[waiting, intermediate, result, result],
            ),
            patch("autoboot_usb.services.write_worker.LOG.exception"),
        ):
            worker.run()
        if worker.session:
            shutil.rmtree(worker.session)
        return completed, failed, cancelled, progress, kernel.closed

    def valid_result(self):
        return {
            "state": "complete",
            "sha256": self.digest,
            "target": self.target.device_id,
            "count": self.path.stat().st_size,
            "total": self.path.stat().st_size,
        }

    def test_completion_requires_success_exit_and_matching_checksum_target_size(self):
        complete, fail, cancel, progress, closed = self.execute(self.valid_result())
        self.assertEqual(complete, [self.digest])
        self.assertFalse(fail or cancel)
        self.assertIn(("admin_approved", 0, 0), progress)
        self.assertIn(("confirming_usb", 0, 0), progress)
        self.assertIn(("checking", 5_368_709_120, 6_442_450_944), progress)
        self.assertNotIn("complete", [phase for phase, *_ in progress])
        self.assertEqual(closed, [123])

    def test_startup_failure_never_requests_elevation(self):
        with patch("autoboot_usb.services.write_worker.launch_elevated") as launch:
            worker = UsbWriteWorker(self.image, self.target)
            failed = []
            worker.failed.connect(failed.append)
            with (
                patch("autoboot_usb.services.write_worker.sys.platform", "win32"),
                patch(
                    "autoboot_usb.services.write_worker.check_helper_startup",
                    side_effect=RuntimeError("Writer import failed"),
                ),
                patch("autoboot_usb.services.write_worker.LOG.exception"),
            ):
                worker.run()
            self.addCleanup(shutil.rmtree, worker.session)
        launch.assert_not_called()
        self.assertIn("Writer import failed", failed[0])

    def test_preflight_uses_exact_helper_without_elevation_and_surfaces_diagnostics(self):
        import json
        from types import SimpleNamespace

        session = Path(self.temp.name)
        (session / "ready.jsonl").write_text('{"state":"ready"}\n', encoding="utf-8")
        with patch(
            "autoboot_usb.services.write_worker.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stderr=""),
        ) as run:
            check_helper_startup(
                "pythonw.exe", ["-I", "-S", "run_writer.py", "request", "digest"], session
            )
        self.assertEqual(
            run.call_args.args[0],
            ["pythonw.exe", "-I", "-S", "run_writer.py", "--check", "request", "digest"],
        )
        (session / "status.jsonl").write_text(
            json.dumps({"state": "failed", "message": "ModuleNotFoundError: writer module"}) + "\n",
            encoding="utf-8",
        )
        with (
            patch(
                "autoboot_usb.services.write_worker.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stderr=""),
            ),
            self.assertRaisesRegex(RuntimeError, "ModuleNotFoundError"),
        ):
            check_helper_startup("pythonw.exe", ["request", "digest"], session)

    def test_failed_process_or_forged_result_is_not_success(self):
        cases = [
            (self.valid_result(), 1),
            ({**self.valid_result(), "sha256": "0" * 64}, 0),
            ({**self.valid_result(), "target": "PhysicalDrive3"}, 0),
            ({**self.valid_result(), "count": 1}, 0),
            ({**self.valid_result(), "total": 1}, 0),
            ({}, 0),
        ]
        for result, code in cases:
            complete, fail, *_ = self.execute(result, code)
            self.assertFalse(complete)
            self.assertTrue(fail)

    def test_helper_cancel_is_reported(self):
        complete, fail, cancelled, *_ = self.execute(
            {"state": "cancelled", "message": "No image was written"}, 1
        )
        self.assertFalse(complete or fail)
        self.assertEqual(cancelled, ["No image was written"])

    def test_uac_denial_is_reported_without_success(self):
        complete, failed, cancelled, *_ = self.execute(
            {}, elevation_error=PermissionError("Approval cancelled")
        )
        self.assertFalse(complete or cancelled)
        self.assertIn("Approval cancelled", failed[0])

    def test_unreadable_status_cancels_helper_and_waits_for_its_exit(self):
        worker = UsbWriteWorker(self.image, self.target)
        completed, failed = [], []
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)
        kernel = Kernel()
        with (
            patch("autoboot_usb.services.write_worker.sys.platform", "win32"),
            patch("autoboot_usb.services.write_worker.check_helper_startup"),
            patch("autoboot_usb.services.write_worker.launch_elevated", return_value=123),
            patch("autoboot_usb.services.write_worker.process_api", return_value=kernel),
            patch(
                "autoboot_usb.services.write_worker._read_status",
                side_effect=PermissionError("Windows denied access to status.jsonl"),
            ),
            patch("autoboot_usb.services.write_worker.LOG.exception"),
        ):
            worker.run()
        self.addCleanup(shutil.rmtree, worker.session)
        self.assertFalse(completed)
        self.assertIn("Windows denied access", failed[0])
        self.assertTrue((worker.session / "cancel").is_file())
        self.assertEqual((worker.session / "channel_ack.jsonl").stat().st_size, 0)
        self.assertEqual(kernel.closed, [123])

    def test_native_launcher_reports_cancelled_uac(self):
        from types import SimpleNamespace

        shell = SimpleNamespace(
            ShellExecuteExW=Function(lambda *_: False), IsUserAnAdmin=Function(lambda: False)
        )
        with (
            patch(
                "autoboot_usb.services.write_worker.ctypes.WinDLL", return_value=shell, create=True
            ),
            patch(
                "autoboot_usb.services.write_worker.ctypes.get_last_error",
                return_value=1223,
                create=True,
            ),
            self.assertRaisesRegex(PermissionError, "Administrator approval was cancelled"),
        ):
            launch_elevated("helper.exe", ["request", "digest"])

    def test_exited_helper_without_status_reports_actionable_error(self):
        complete, failed, *_ = self.execute({}, exit_code=2)
        self.assertFalse(complete)
        self.assertIn("without reporting its status", failed[0])
        self.assertIn("exit code 2", failed[0])

    def test_windows_elevated_launcher_does_not_hide_confirmation(self):
        from types import SimpleNamespace

        capture = {}

        def execute(info_pointer):
            info = info_pointer._obj
            capture.update(
                show=info.nShow,
                verb=info.lpVerb,
                mask=info.fMask,
                file=info.lpFile,
                parameters=info.lpParameters,
            )
            info.hProcess = 123
            return True

        shell = SimpleNamespace(
            ShellExecuteExW=Function(execute), IsUserAnAdmin=Function(lambda: False)
        )
        with patch(
            "autoboot_usb.services.write_worker.ctypes.WinDLL", return_value=shell, create=True
        ):
            handle = launch_elevated(
                r"C:\Program Files\Python\pythonw.exe",
                [r"C:\Users\Test User\AppData\Local\Temp\request.json", "f" * 64],
            )
        self.assertEqual(handle, 123)
        self.assertEqual(capture["show"], 5)
        self.assertEqual(capture["verb"], "runas")
        self.assertEqual(capture["mask"], 0x140)
        self.assertIn("request.json", capture["parameters"])

    def test_already_elevated_app_does_not_request_uac_again(self):
        from types import SimpleNamespace

        capture = {}

        def execute(info_pointer):
            info = info_pointer._obj
            capture["verb"] = info.lpVerb
            info.hProcess = 123
            return True

        shell = SimpleNamespace(
            ShellExecuteExW=Function(execute), IsUserAnAdmin=Function(lambda: True)
        )
        with patch(
            "autoboot_usb.services.write_worker.ctypes.WinDLL", return_value=shell, create=True
        ):
            self.assertEqual(launch_elevated("helper.exe", ["request", "digest"]), 123)
        self.assertEqual(capture["verb"], "open")

    def test_source_helper_path_is_absolute_and_python_is_isolated(self):
        executable, args = helper_command(Path(self.temp.name), "a" * 64)
        self.assertEqual(args[:2], ["-I", "-S"])
        self.assertTrue(Path(args[2]).is_file())
        self.assertEqual(Path(args[2]).name, "run_writer.py")

    def test_windows_source_uses_windowless_python_when_available(self):
        python = Path(self.temp.name) / "python.exe"
        pythonw = python.with_name("pythonw.exe")
        python.touch()
        pythonw.touch()
        with (
            patch("autoboot_usb.services.write_worker.sys.platform", "win32"),
            patch("autoboot_usb.services.write_worker.sys.executable", str(python)),
            patch("autoboot_usb.services.write_worker.sys.prefix", "same-prefix"),
            patch("autoboot_usb.services.write_worker.sys.base_prefix", "same-prefix"),
        ):
            executable, args = helper_command(Path(self.temp.name), "a" * 64)
        self.assertEqual(executable, str(pythonw))
        self.assertEqual(args[0], "-I")

    def test_windows_venv_elevates_base_interpreter_directly(self):
        base = Path(self.temp.name) / "base" / "python.exe"
        base.parent.mkdir()
        base.touch()
        base.with_name("pythonw.exe").touch()
        with (
            patch("autoboot_usb.services.write_worker.sys.platform", "win32"),
            patch(
                "autoboot_usb.services.write_worker.sys.executable",
                str(base.parent / ".venv" / "python.exe"),
            ),
            patch("autoboot_usb.services.write_worker.sys._base_executable", str(base)),
            patch("autoboot_usb.services.write_worker.sys.prefix", "venv"),
            patch("autoboot_usb.services.write_worker.sys.base_prefix", "base"),
        ):
            executable, args = helper_command(Path(self.temp.name), "a" * 64)
        self.assertEqual(executable, str(base.with_name("pythonw.exe")))
        self.assertEqual(args[:2], ["-I", "-S"])
