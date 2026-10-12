"""Read-only scan timeout, cancellation and automatic admin discovery."""

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autoboot_usb.adapters import windows
from autoboot_usb.adapters.windows_tools import run_command
from autoboot_usb.models import UsbDevice
from autoboot_usb.services import elevated_discovery
from autoboot_usb.services.checksum import OperationCancelled
from autoboot_usb.services.writer_session import create_session, read_message


class DiscoveryTests(unittest.TestCase):
    def test_scan_uses_filtered_cim_query_and_allows_more_than_16_seconds(self):
        with patch.object(windows, "powershell", return_value="[]") as run:
            self.assertEqual(windows.WindowsAdapter().list_usb_disks(), [])
        self.assertEqual(run.call_args.kwargs["timeout"], 90)
        self.assertIn("-Filter 'BusType = 7'", run.call_args.args[0])
        self.assertIn("-OperationTimeoutSec 60", run.call_args.args[0])

    def test_timeout_error_does_not_dump_the_powershell_command(self):
        with patch.object(windows, "powershell", side_effect=TimeoutError("timeout")):
            with self.assertRaisesRegex(windows.UsbDiscoveryError, "90 seconds") as raised:
                windows.WindowsAdapter().list_usb_disks()
        self.assertNotIn("-Command", str(raised.exception))

    def test_permission_result_requires_admin_but_other_errors_do_not(self):
        for kind, expected in (
            ("access_denied", windows.DiscoveryPermissionRequired),
            ("storage", windows.UsbDiscoveryError),
        ):
            with patch.object(
                windows,
                "powershell",
                return_value=json.dumps({"AutoBootError": kind, "Message": "failure"}),
            ):
                with self.assertRaises(expected):
                    windows.WindowsAdapter().list_usb_disks()

    def test_gui_scanner_requests_uac_only_for_permission_denial(self):
        from PySide6.QtCore import QCoreApplication

        from autoboot_usb.ui.window import DiskScanner

        app = QCoreApplication.instance() or QCoreApplication([])
        adapter = windows.WindowsAdapter()
        device = UsbDevice(
            "PhysicalDrive2", "Test USB", 16_000_000_000, "usb", True, False, serial="TEST-2"
        )
        scanner = DiskScanner(adapter)
        found = []
        scanner.completed.connect(found.append)
        with (
            patch.object(
                adapter, "list_usb_disks", side_effect=windows.DiscoveryPermissionRequired("denied")
            ),
            patch(
                "autoboot_usb.services.admin_discovery.discover_with_admin", return_value=[device]
            ) as admin,
        ):
            scanner.run()
        app.processEvents()
        self.assertEqual(found, [[device]])
        admin.assert_called_once()
        with (
            patch.object(
                adapter, "list_usb_disks", side_effect=windows.UsbDiscoveryError("timeout")
            ),
            patch("autoboot_usb.services.admin_discovery.discover_with_admin") as admin,
            patch("autoboot_usb.ui.window.LOG.warning"),
        ):
            scanner.run()
        admin.assert_not_called()

    def test_readonly_command_timeout_and_cancellation_terminate_the_child(self):
        command = [sys.executable, "-I", "-S", "-c", "import time;time.sleep(20)"]
        with self.assertRaises(TimeoutError):
            run_command(command, timeout=0.15)
        with self.assertRaises(OperationCancelled):
            run_command(command, cancel=lambda: True, timeout=90)

    def test_discovery_bootstrap_check_accepts_only_readonly_operation(self):
        root = Path(__file__).resolve().parents[1]
        session = create_session()
        self.addCleanup(shutil.rmtree, session)
        for payload, expected in (({"operation": "discover"}, 0), ({"operation": "format"}, 1)):
            raw = json.dumps(payload).encode()
            request = session / "request.json"
            request.write_bytes(raw)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(root / "run_writer.py"),
                    "--discover",
                    "--check",
                    str(request),
                    hashlib.sha256(raw).hexdigest(),
                ],
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_privileged_discovery_enumerates_without_erasure_or_disk_writer(self):
        session = create_session()
        self.addCleanup(shutil.rmtree, session)
        device = UsbDevice(
            "PhysicalDrive2", "Test USB", 16_000_000_000, "usb", True, False, serial="TEST-2"
        )
        with (
            patch.object(elevated_discovery, "await_status_ack"),
            patch.object(elevated_discovery, "WindowsAdapter") as adapter,
            patch.object(
                ctypes,
                "windll",
                SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
                create=True,
            ),
            patch("autoboot_usb.services.elevated_writer.confirm_erase") as erase,
            patch("autoboot_usb.services.elevated_writer.create_bootable_usb") as write,
        ):
            adapter.return_value.list_usb_disks.return_value = [device]
            self.assertEqual(elevated_discovery.discover_main(session), 0)
            erase.assert_not_called()
            write.assert_not_called()
        self.assertEqual(read_message(session)["devices"], [vars(device)])


@unittest.skipUnless(os.name == "nt", "Native Windows filtered disk query")
class NativeDiscoveryTests(unittest.TestCase):
    def test_native_cim_query_returns_only_eligible_usb_disks(self):
        devices = windows.WindowsAdapter().list_usb_disks()
        self.assertTrue(all(d.safe_to_offer and d.bus == "usb" for d in devices))
