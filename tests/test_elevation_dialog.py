"""Safely exercise the elevated confirmation without UAC or a real USB."""

import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from autoboot_usb.catalogue import BY_KEY
from autoboot_usb.models import UsbDevice
from autoboot_usb.services import elevated_writer


class ElevationConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="autoboot-write-")
        self.addCleanup(self.temp.cleanup)
        self.session = Path(self.temp.name)
        self.image = self.session / "test.iso"
        self.image.write_bytes(b"fake ISO (the writer must be mocked)")
        self.release = BY_KEY["kali"].releases[0]
        self.target = UsbDevice(
            "PhysicalDrive2", "Test USB", 16_000_000_000, "usb", True, False, serial="TEST-2"
        )
        self.data = {
            "image": str(self.image),
            "image_size": self.image.stat().st_size,
            "sha256": self.release.sha256,
            "target": {},
        }

    def test_confirmation_is_topmost_foreground_and_defaults_to_no(self):
        dialog = Mock(return_value=6)
        with patch.object(
            ctypes,
            "windll",
            SimpleNamespace(user32=SimpleNamespace(MessageBoxW=dialog)),
            create=True,
        ):
            accepted = elevated_writer.confirm_erase(self.data, self.target)
        self.assertTrue(accepted)
        self.assertEqual(dialog.call_args.args[3], elevated_writer.ERASE_CONFIRM_FLAGS)
        flags = dialog.call_args.args[3]
        for flag in (0x4, 0x30, 0x100, 0x10000, 0x40000):
            self.assertEqual(flags & flag, flag)
        self.assertIn("PhysicalDrive2", dialog.call_args.args[1])
        self.assertIn("TEST-2", dialog.call_args.args[1])
        self.assertIn("test.iso", dialog.call_args.args[1])

    def test_declining_final_confirmation_never_calls_writer(self):
        dialog = Mock(return_value=7)  # Windows IDNO
        with patch.object(
            ctypes,
            "windll",
            SimpleNamespace(user32=SimpleNamespace(MessageBoxW=dialog)),
            create=True,
        ):
            self.assertFalse(elevated_writer.confirm_erase(self.data, self.target))

    def test_helper_reports_confirmation_stage_before_showing_dialog(self):
        data = {**self.data, "target": vars(self.target)}
        request_path = self.session / "request.json"
        request_path.write_text(json.dumps(data), encoding="utf-8")
        observed = []

        def confirmation(_, __):
            observed.append(
                json.loads((self.session / "status.json").read_text(encoding="utf-8"))["state"]
            )
            return True

        def simulated_writer(*args, **kwargs):
            observed.append("writer_called")
            kwargs["progress"]("checking", self.image.stat().st_size, self.image.stat().st_size)
            return self.release.sha256

        with (
            patch.object(elevated_writer.sys, "platform", "win32"),
            patch.object(elevated_writer, "read_request", return_value=data),
            patch.object(elevated_writer, "await_status_ack"),
            patch.object(elevated_writer, "confirm_erase", side_effect=confirmation),
            patch.object(elevated_writer, "create_bootable_usb", side_effect=simulated_writer),
            patch("autoboot_usb.adapters.windows_writer.reject_target_files"),
            patch.object(
                ctypes,
                "windll",
                SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
                create=True,
            ),
        ):
            exit_code = elevated_writer.main([str(request_path), "f" * 64])
        self.assertEqual(exit_code, 0)
        self.assertEqual(observed[:2], ["confirming_usb", "writer_called"])
        status = json.loads((self.session / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "complete")

    def test_cancel_during_confirmation_cannot_start_a_write(self):
        data = {**self.data, "target": vars(self.target)}
        request_path = self.session / "request.json"
        request_path.write_text(json.dumps(data), encoding="utf-8")

        def simulate_cancel(*_args):
            (self.session / "cancel").touch()
            return True  # User clicks Yes *after* cancelling in the main GUI.

        with (
            patch.object(elevated_writer.sys, "platform", "win32"),
            patch.object(elevated_writer, "read_request", return_value=data),
            patch.object(elevated_writer, "await_status_ack"),
            patch.object(elevated_writer, "confirm_erase", side_effect=simulate_cancel),
            patch.object(elevated_writer, "create_bootable_usb") as write,
            patch("autoboot_usb.adapters.windows_writer.reject_target_files"),
            patch.object(
                ctypes,
                "windll",
                SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
                create=True,
            ),
        ):
            exit_code = elevated_writer.main([str(request_path), "f" * 64])
        self.assertEqual(exit_code, 1)
        write.assert_not_called()
        status = json.loads((self.session / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
