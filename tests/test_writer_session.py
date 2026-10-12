"""Status access and UAC communication regressions. Never open a physical disk."""

import ctypes
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autoboot_usb.catalogue import BY_KEY
from autoboot_usb.models import UsbDevice
from autoboot_usb.services import elevated_writer
from autoboot_usb.services.checksum import OperationCancelled
from autoboot_usb.services.writer_session import (
    WindowsSessionPermissions,
    atomic_publish,
    create_session,
    read_message,
)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = create_session()
        self.addCleanup(shutil.rmtree, self.session)

    def test_repeated_atomic_status_replacements_remain_readable(self):
        for count in range(4):
            payload = {"state": "writing", "count": count, "total": 5_000_000_000}
            atomic_publish(self.session, "status.json", payload)
            self.assertEqual(read_message(self.session), payload)
        self.assertFalse(list(self.session.glob("*.tmp")))

    def test_unreadable_status_is_an_access_error_not_missing_status(self):
        atomic_publish(self.session, "status.json", {"state": "failed"})
        with (
            patch.object(Path, "open", side_effect=PermissionError("Access is denied")),
            self.assertRaisesRegex(PermissionError, "Windows denied access.*status.json"),
        ):
            read_message(self.session)

    def test_missing_status_is_pending_and_other_io_errors_are_not_hidden(self):
        self.assertEqual(read_message(self.session), {})
        atomic_publish(self.session, "status.json", {"state": "writing"})
        with (
            patch.object(Path, "open", side_effect=OSError("Device unavailable")),
            self.assertRaisesRegex(OSError, "Device unavailable"),
        ):
            read_message(self.session)

    def test_invalid_oversized_and_reparse_messages_are_refused(self):
        with self.assertRaises(ValueError):
            atomic_publish(self.session, "../status.json", {})
        with self.assertRaises(ValueError):
            atomic_publish(self.session, "status.json", {"message": "x" * 16384})
        path = self.session / "status.json"
        path.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_message(self.session)
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError):
            read_message(self.session)

    def test_handshake_requires_matching_fresh_nonce(self):
        nonce = "a" * 32
        with patch.object(elevated_writer.secrets, "token_hex", return_value=nonce):
            for payload in ({}, {"nonce": "b" * 32}):
                atomic_publish(self.session, "channel_ack.json", payload)
                with self.assertRaisesRegex(PermissionError, "before any disk was opened"):
                    elevated_writer.await_status_ack(self.session, lambda: False, timeout=0)
            atomic_publish(self.session, "channel_ack.json", {"nonce": nonce})
            elevated_writer.await_status_ack(self.session, lambda: False, timeout=0)
        self.assertEqual(read_message(self.session)["nonce"], nonce)

    def test_cancellation_during_handshake_stops_before_confirmation(self):
        with self.assertRaises(OperationCancelled):
            elevated_writer.await_status_ack(self.session, lambda: True)

    def test_missing_ack_never_confirms_erasure_or_calls_disk_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            data = {
                "image": str(Path(folder) / "not-opened.iso"),
                "image_size": 1024,
                "sha256": BY_KEY["kali"].releases[0].sha256,
                "target": vars(
                    UsbDevice(
                        "PhysicalDrive2",
                        "Test USB",
                        16_000_000_000,
                        "usb",
                        True,
                        False,
                        serial="TEST-2",
                    )
                ),
            }
            wait = elevated_writer.await_status_ack
            with (
                patch.object(elevated_writer.sys, "platform", "win32"),
                patch.object(elevated_writer, "read_request", return_value=data),
                patch.object(
                    elevated_writer,
                    "await_status_ack",
                    side_effect=lambda session, cancel: wait(session, cancel, timeout=0),
                ),
                patch.object(elevated_writer, "confirm_erase") as confirm,
                patch.object(elevated_writer, "create_bootable_usb") as write,
                patch.object(
                    ctypes,
                    "windll",
                    SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
                    create=True,
                ),
            ):
                result = elevated_writer.main([str(self.session / "request.json"), "f" * 64])
            self.assertEqual(result, 1)
            confirm.assert_not_called()
            write.assert_not_called()
            status = read_message(self.session)
            self.assertEqual(status["state"], "failed")
            self.assertIn("No image was written", status["message"])
            self.assertFalse(Path(data["image"]).exists())


@unittest.skipUnless(os.name == "nt", "Native Windows ACL check")
class WindowsAclTests(unittest.TestCase):
    def setUp(self):
        self.permissions = WindowsSessionPermissions()
        self.session = create_session()
        self.addCleanup(shutil.rmtree, self.session)

    def dacl(self, path):
        api = self.permissions
        w = api.w
        descriptor, string = w.LPVOID(), w.LPWSTR()
        error = api.advapi.GetNamedSecurityInfoW(
            str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor)
        )
        self.assertEqual(error, 0)
        convert = api.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [
            w.LPVOID,
            w.DWORD,
            w.DWORD,
            ctypes.POINTER(w.LPWSTR),
            ctypes.POINTER(w.DWORD),
        ]
        convert.restype = w.BOOL
        try:
            self.assertTrue(convert(descriptor, 1, 4, ctypes.byref(string), None))
            return string.value
        finally:
            if string:
                api.kernel.LocalFree(ctypes.cast(string, w.HLOCAL))
            api.kernel.LocalFree(descriptor)

    def assert_private(self, path):
        sddl = self.dacl(path)
        sid = self.permissions.current_user_sid()
        identities = {ace.split(";")[-1] for ace in sddl.split("(")[1:]}
        identities = {identity.rstrip(")") for identity in identities}
        self.assertEqual(identities, {sid, "SY", "BA"})
        self.assertTrue(sddl.startswith("D:P"), sddl)

    def test_directory_acl_uses_original_user_and_inheritable_private_grants(self):
        self.assert_private(self.session)
        self.assertEqual(self.dacl(self.session).count("OICI"), 3)

    def test_all_helper_message_files_preserve_original_gui_user_access(self):
        for filename in ("ready.json", "status.json", "channel_ack.json", "status.json"):
            payload = {"state": "test"}
            atomic_publish(self.session, filename, payload)
            self.assertEqual(read_message(self.session, filename), payload)
            self.assert_private(self.session / filename)


if __name__ == "__main__":
    unittest.main()
