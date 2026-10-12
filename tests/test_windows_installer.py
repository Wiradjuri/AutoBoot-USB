"""Windows installation files and native DISM; never formats a storage device."""

import ctypes
import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autoboot_usb.adapters import windows_installer as installer
from autoboot_usb.models import UsbDevice
from autoboot_usb.services.checksum import OperationCancelled
from autoboot_usb.services.writer import WriteSafetyError


def create_files(root):
    for name, data in {
        "efi/boot/bootx64.efi": b"efi",
        "boot/bcd": b"boot config",
        "sources/boot.wim": b"boot image",
        "sources/install.wim": b"installation files" * 500,
        "setup.exe": b"setup",
        "empty.txt": b"",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.destination = Path(self.temp.name) / "usb-files"
        self.source.mkdir()
        self.destination.mkdir()
        create_files(self.source)

    def copy(self):
        phases = []
        manifest, total = installer.copy_windows_files(
            installer.windows_file_plan(self.source),
            self.destination,
            lambda *value: phases.append(value),
        )
        return manifest, total, phases

    def test_every_file_is_copied_flushed_reopened_and_hashed(self):
        manifest, total, phases = self.copy()
        installer.verify_windows_files(
            manifest, self.destination, total, lambda *value: phases.append(value)
        )
        self.assertEqual({p[0] for p in phases}, {"writing", "flushing", "verifying", "complete"})
        for relative, size, digest in manifest:
            data = (self.destination / relative).read_bytes()
            self.assertEqual(len(data), size)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        self.assertEqual(phases[-1], ("complete", total, total))

    def test_corruption_or_appended_bytes_never_complete(self):
        manifest, total, _ = self.copy()
        path = self.destination / "setup.exe"
        for data in (b"wrong", b"setup-extra"):
            path.write_bytes(data)
            phases = []
            with self.assertRaisesRegex(WriteSafetyError, "verification failed"):
                installer.verify_windows_files(
                    manifest, self.destination, total, lambda *value: phases.append(value)
                )
            self.assertNotIn("complete", [p[0] for p in phases])

    def test_cancel_before_copy_changes_no_files(self):
        with self.assertRaises(OperationCancelled):
            installer.copy_windows_files(
                installer.windows_file_plan(self.source), self.destination, cancel=lambda: True
            )
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_missing_boot_files_reparse_and_path_escape_are_refused(self):
        (self.source / "setup.exe").unlink()
        with self.assertRaisesRegex(WriteSafetyError, "missing"):
            installer.windows_file_plan(self.source)
        with self.assertRaisesRegex(WriteSafetyError, "file path"):
            installer.copy_windows_files(
                [(self.source / "empty.txt", Path("../escape"))], self.destination
            )

    def test_oversized_non_wim_is_refused_before_formatting(self):
        with patch.object(installer, "FAT32_MAX_FILE", 10):
            (self.source / "setup.exe").write_bytes(b"x" * 11)
            with self.assertRaisesRegex(WriteSafetyError, "too large"):
                installer.windows_file_plan(self.source)

    def test_split_payload_requires_nonempty_bounded_output(self):
        scratch = Path(self.temp.name) / "scratch"
        scratch.mkdir()
        with (
            patch.object(installer, "FAT32_MAX_FILE", 1024),
            patch.object(installer, "system_binary", return_value="dism.exe"),
            patch.object(installer, "run_command"),
        ):
            with self.assertRaisesRegex(WriteSafetyError, "did not produce"):
                installer.split_windows_payload(
                    installer.windows_file_plan(self.source), scratch, None
                )

    def test_format_rejects_capacity_and_unexpected_volume_results(self):
        target = UsbDevice(
            "PhysicalDrive2",
            "Test USB",
            16_000_000_000,
            "usb",
            True,
            False,
            serial="TEST-2",
            unique_id="ID-2",
        )
        with patch.object(installer, "powershell") as command:
            with self.assertRaisesRegex(WriteSafetyError, "capacity"):
                installer.format_windows_usb(replace(target, size_bytes=1_000_000), 1_000_000)
            command.assert_not_called()
        good = {
            "path": "\\\\?\\Volume{01234567-89ab-cdef-0123-456789abcdef}\\",
            "letter": "G",
            "size": 15_000_000_000,
        }
        with patch.object(installer, "powershell", return_value=json.dumps(good)) as command:
            self.assertEqual(installer.format_windows_usb(target, 2_000_000_000), good)
            self.assertEqual(command.call_args.args[1]["target"]["serial"], target.serial)
        for result in ({**good, "path": "C:\\"}, {**good, "size": 1}, {**good, "letter": "GG"}):
            with patch.object(installer, "powershell", return_value=json.dumps(result)):
                with self.assertRaisesRegex(WriteSafetyError, "unexpected"):
                    installer.format_windows_usb(target, 2_000_000_000)

    def test_mount_cleanup_on_cancel_or_invalid_mount_path(self):
        for result in ({"root": "G:\\", "owned": True}, {"root": "bad", "owned": True}):
            with patch.object(installer, "powershell", return_value=json.dumps(result)) as command:
                with self.assertRaises((WriteSafetyError, OperationCancelled)):
                    with installer.mounted_iso(self.source, lambda: result["root"] == "G:\\"):
                        self.fail("Invalid/cancelled mount should not be yielded")
                # A cancellation before mounting has nothing to clean up.
                if result["root"] == "bad":
                    self.assertIn("Dismount-DiskImage", command.call_args.args[0])


@unittest.skipUnless(os.name == "nt", "Native Windows installer components")
class WindowsNativeInstallerTests(unittest.TestCase):
    def test_storage_script_parses_without_executing_it(self):
        script = (
            "$tokens=$null;$errors=$null;"
            "$null=[Management.Automation.Language.Parser]::ParseInput($r.script,[ref]$tokens,[ref]$errors);"
            "if($errors.Count){throw ($errors | Out-String)};'parsed'"
        )
        result = installer.powershell(script, {"script": installer.FORMAT_SCRIPT})
        self.assertEqual(result.strip(), "parsed")

    def test_real_dism_capture_split_and_file_readback(self):
        if not ctypes.windll.shell32.IsUserAnAdmin():
            self.skipTest("DISM capture requires an elevated test runner")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, scratch, destination = root / "captured-files", root / "split", root / "copied"
            for path in (source, scratch, destination):
                path.mkdir()
            for index in range(6):
                (source / f"fixture-{index}.bin").write_bytes(os.urandom(600_000))
            wim = root / "install.wim"
            installer.run_command(
                [
                    installer.system_binary("dism.exe"),
                    "/English",
                    "/Capture-Image",
                    f"/ImageFile:{wim}",
                    f"/CaptureDir:{source}",
                    "/Name:Fixture",
                    "/Compress:none",
                ]
            )
            with patch.object(installer, "FAT32_MAX_FILE", 2 * 1024**2):
                plan = installer.split_windows_payload(
                    [(wim, Path("sources/install.wim"))], scratch, None
                )
                self.assertGreater(len(plan), 1)
                self.assertTrue(all(p.stat().st_size <= installer.FAT32_MAX_FILE for p, _ in plan))
            manifest, total = installer.copy_windows_files(plan, destination)
            installer.verify_windows_files(manifest, destination, total)
