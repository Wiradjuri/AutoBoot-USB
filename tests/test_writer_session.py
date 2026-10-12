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
    create_session,
    publish_message,
    read_message,
)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = create_session()
        self.addCleanup(shutil.rmtree, self.session)

    def test_repeated_status_appends_keep_the_original_file(self):
        inode = (self.session / "status.jsonl").stat().st_ino
        for count in range(4):
            payload = {"state": "writing", "count": count, "total": 5_000_000_000}
            publish_message(self.session, "status.jsonl", payload)
            self.assertEqual(read_message(self.session), payload)
            self.assertEqual((self.session / "status.jsonl").stat().st_ino, inode)
        self.assertFalse(list(self.session.glob("*.tmp")))

    def test_unreadable_status_is_an_access_error_not_missing_status(self):
        publish_message(self.session, "status.jsonl", {"state": "failed"})
        with (
            patch.object(Path, "open", side_effect=PermissionError("Access is denied")),
            self.assertRaisesRegex(PermissionError, "Windows denied access.*status.jsonl"),
        ):
            read_message(self.session)

    def test_missing_status_is_pending_and_other_io_errors_are_not_hidden(self):
        self.assertEqual(read_message(self.session), {})
        publish_message(self.session, "status.jsonl", {"state": "writing"})
        with (
            patch.object(Path, "open", side_effect=OSError("Device unavailable")),
            self.assertRaisesRegex(OSError, "Device unavailable"),
        ):
            read_message(self.session)

    def test_invalid_oversized_and_reparse_messages_are_refused(self):
        with self.assertRaises(ValueError):
            publish_message(self.session, "../status.jsonl", {})
        with self.assertRaises(ValueError):
            publish_message(self.session, "status.jsonl", {"message": "x" * 16384})
        path = self.session / "status.jsonl"
        path.write_text("[]\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_message(self.session)
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError):
            read_message(self.session)

    def test_unfinished_update_keeps_previous_message_until_newline(self):
        path = self.session / "status.jsonl"
        publish_message(self.session, "status.jsonl", {"state": "writing", "count": 1})
        with path.open("ab") as output:
            output.write(b'{"state":"complete","count":2}')
        self.assertEqual(read_message(self.session), {"state": "writing", "count": 1})
        with path.open("ab") as output:
            output.write(b"\n")
        self.assertEqual(read_message(self.session), {"state": "complete", "count": 2})

    def test_long_journal_reads_the_latest_message(self):
        for count in range(50):
            publish_message(self.session, "status.jsonl", {"count": count, "message": "x" * 2000})
        self.assertGreater((self.session / "status.jsonl").stat().st_size, 2 * 16384)
        self.assertEqual(read_message(self.session)["count"], 49)

    def test_helper_does_not_recreate_a_deleted_channel(self):
        (self.session / "status.jsonl").unlink()
        with self.assertRaises(FileNotFoundError):
            publish_message(self.session, "status.jsonl", {"state": "writing"})
        self.assertFalse((self.session / "status.jsonl").exists())

    def test_brief_windows_access_denial_is_retried(self):
        publish_message(self.session, "status.jsonl", {"state": "writing"})
        error = PermissionError("Access is denied")
        error.winerror = 5
        original = Path.open
        calls = []

        def interrupted(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise error
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", interrupted):
            self.assertEqual(read_message(self.session), {"state": "writing"})
        self.assertEqual(len(calls), 2)

    def test_plain_crt_eacces_is_retried_without_a_winerror(self):
        publish_message(self.session, "status.jsonl", {"state": "checking"})
        original = Path.open
        calls = []

        def interrupted(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError(13, "Permission denied")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", interrupted):
            self.assertEqual(read_message(self.session), {"state": "checking"})
        self.assertEqual(len(calls), 2)

    def test_handshake_requires_matching_fresh_nonce(self):
        nonce = "a" * 32
        with patch.object(elevated_writer.secrets, "token_hex", return_value=nonce):
            for payload in ({}, {"nonce": "b" * 32}):
                publish_message(self.session, "channel_ack.jsonl", payload)
                with self.assertRaisesRegex(PermissionError, "before any disk was opened"):
                    elevated_writer.await_status_ack(self.session, lambda: False, timeout=0)
            publish_message(self.session, "channel_ack.jsonl", {"nonce": nonce})
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
        # SDDL may render a RID-500 account as LA. Compare the actual ACE SIDs,
        # not machine-dependent aliases or localised account names.
        api, w = self.permissions, self.permissions.w
        descriptor, dacl = w.LPVOID(), w.LPVOID()
        error = api.advapi.GetNamedSecurityInfoW(
            str(path), 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
        )
        self.assertEqual(error, 0)

        class AclSizeInfo(ctypes.Structure):
            _fields_ = [("count", w.DWORD), ("used", w.DWORD), ("free", w.DWORD)]

        acl_info = api.advapi.GetAclInformation
        acl_info.argtypes = [w.LPVOID, w.LPVOID, w.DWORD, ctypes.c_int]
        acl_info.restype = w.BOOL
        get_ace = api.advapi.GetAce
        get_ace.argtypes = [w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID)]
        get_ace.restype = w.BOOL
        info, identities = AclSizeInfo(), set()
        try:
            self.assertTrue(acl_info(dacl, ctypes.byref(info), ctypes.sizeof(info), 2))
            self.assertEqual(info.count, 3)
            for index in range(info.count):
                ace, string = w.LPVOID(), w.LPWSTR()
                self.assertTrue(get_ace(dacl, index, ctypes.byref(ace)))
                self.assertEqual(ctypes.c_ubyte.from_address(ace.value).value, 0)
                # ACCESS_ALLOWED_ACE: 4-byte ACE_HEADER, DWORD mask, then SID.
                self.assertTrue(
                    api.advapi.ConvertSidToStringSidW(w.LPVOID(ace.value + 8), ctypes.byref(string))
                )
                try:
                    identities.add(string.value)
                finally:
                    api.kernel.LocalFree(ctypes.cast(string, w.HLOCAL))
        finally:
            api.kernel.LocalFree(descriptor)
        self.assertEqual(identities, {api.current_user_sid(), "S-1-5-18", "S-1-5-32-544"})
        self.assertTrue(sddl.startswith("D:P"), sddl)

    def test_directory_acl_uses_original_user_and_inheritable_private_grants(self):
        self.assert_private(self.session)
        self.assertEqual(self.dacl(self.session).count("OICI"), 3)

    def test_all_helper_message_files_preserve_original_gui_user_access(self):
        for filename in ("ready.jsonl", "status.jsonl", "channel_ack.jsonl", "status.jsonl"):
            payload = {"state": "test"}
            publish_message(self.session, filename, payload)
            self.assertEqual(read_message(self.session, filename), payload)
            self.assert_private(self.session / filename)


if __name__ == "__main__":
    unittest.main()
