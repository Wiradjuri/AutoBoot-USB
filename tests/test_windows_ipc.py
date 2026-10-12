"""A real high-integrity child communicating with a medium-integrity reader."""

import ctypes
import os
import shutil
import subprocess
import sys
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from autoboot_usb.services.writer_session import create_session, publish_message, read_message


class TokenApi:
    def __init__(self):
        from ctypes import wintypes as w

        self.w = w
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.LocalFree.argtypes = [w.LPVOID]
        self.advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        self.advapi.DuplicateTokenEx.argtypes = [
            w.HANDLE,
            w.DWORD,
            w.LPVOID,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(w.HANDLE),
        ]
        self.advapi.GetTokenInformation.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.DWORD),
        ]
        self.advapi.SetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
        self.advapi.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.LPVOID)]
        self.advapi.GetSidSubAuthority.argtypes = [w.LPVOID, w.DWORD]
        self.advapi.GetSidSubAuthority.restype = ctypes.POINTER(w.DWORD)
        self.advapi.GetLengthSid.argtypes = [w.LPVOID]
        self.advapi.ImpersonateLoggedOnUser.argtypes = [w.HANDLE]

    def check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def process_integrity(self):
        token, length = self.w.HANDLE(), self.w.DWORD()
        self.check(
            self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(token))
        )
        try:
            self.advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(length))
            buffer = ctypes.create_string_buffer(length.value)
            self.check(
                self.advapi.GetTokenInformation(
                    token, 25, buffer, len(buffer), ctypes.byref(length)
                )
            )
            sid = ctypes.cast(buffer, ctypes.POINTER(self.w.LPVOID)).contents
            return self.advapi.GetSidSubAuthority(sid, 0).contents.value
        finally:
            self.kernel.CloseHandle(token)

    @contextmanager
    def medium_reader(self):
        w = self.w

        class Label(ctypes.Structure):
            _fields_ = [("sid", w.LPVOID), ("attributes", w.DWORD)]

        original, medium, sid = w.HANDLE(), w.HANDLE(), w.LPVOID()
        try:
            self.check(
                self.advapi.OpenProcessToken(
                    self.kernel.GetCurrentProcess(), 0xF01FF, ctypes.byref(original)
                )
            )
            self.check(
                self.advapi.DuplicateTokenEx(original, 0xF01FF, None, 2, 2, ctypes.byref(medium))
            )
            self.check(self.advapi.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(sid)))
            label = Label(sid, 0x20)
            self.check(
                self.advapi.SetTokenInformation(
                    medium,
                    25,
                    ctypes.byref(label),
                    ctypes.sizeof(label) + self.advapi.GetLengthSid(sid),
                )
            )
            self.check(self.advapi.ImpersonateLoggedOnUser(medium))
            try:
                yield
            finally:
                self.check(self.advapi.RevertToSelf())
        finally:
            if sid:
                self.kernel.LocalFree(sid)
            for token in (medium, original):
                if token:
                    self.kernel.CloseHandle(token)


@unittest.skipUnless(os.name == "nt", "Native Windows integrity-token test")
class WindowsIpcTests(unittest.TestCase):
    def test_medium_gui_reads_high_writer_during_rapid_status_updates(self):
        api = TokenApi()
        if api.process_integrity() < 0x3000:
            self.skipTest("This test needs an elevated Windows test runner")
        with api.medium_reader():
            session = create_session()
        self.addCleanup(shutil.rmtree, session)
        inode = (session / "status.jsonl").stat().st_ino
        with subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                str(Path(__file__).with_name("windows_ipc_fixture.py")),
                str(session),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as child:
            seen = set()
            try:
                with api.medium_reader():
                    deadline = time.monotonic() + 20
                    while child.poll() is None and time.monotonic() < deadline:
                        status = read_message(session)
                        if status.get("state") == "admin_approved":
                            publish_message(
                                session, "channel_ack.jsonl", {"nonce": status["nonce"]}
                            )
                        if "count" in status:
                            seen.add(status["count"])
                        time.sleep(0.001)
                    self.assertIsNotNone(child.poll(), "High-integrity helper did not exit")
                    result = read_message(session)
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, repr(stdout) + repr(stderr))
                self.assertEqual(result["state"], "complete")
                self.assertGreaterEqual(result["integrity"], 0x3000)
                self.assertGreater(len(seen), 10)
                self.assertEqual((session / "status.jsonl").stat().st_ino, inode)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
