"""Private, Qt-free communication between the GUI and the Windows UAC helper."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

MAX_MESSAGE_BYTES = 16384
MESSAGE_FILES = {"status.json", "ready.json", "channel_ack.json"}
# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION. On
# Windows, replacing a file another handle has open, or opening a file while it
# is being replaced (or scanned by antivirus), fails briefly with one of these.
# Status files are rewritten up to ten times a second while the GUI polls them,
# so such overlaps are routine during a multi-minute USB write.
TRANSIENT_WINERRORS = {5, 32, 33}
PUBLISH_RETRY_SECONDS = 5.0
READ_RETRY_SECONDS = 2.0


def _transient(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) and getattr(exc, "winerror", None) in (
        TRANSIENT_WINERRORS
    )


def _replace_with_retry(source: str, destination: Path) -> None:
    deadline = time.monotonic() + PUBLISH_RETRY_SECONDS
    delay = 0.005
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if not _transient(exc) or time.monotonic() >= deadline:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 0.05)


class WindowsSessionPermissions:
    """Use the actual token user SID, rather than an elevated token's owner."""

    def __init__(self):
        from ctypes import wintypes as w

        self.w = w
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        pointer = ctypes.POINTER
        self.kernel.GetCurrentProcess.argtypes = []
        self.kernel.GetCurrentProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.kernel.LocalFree.argtypes = [w.HLOCAL]
        self.kernel.LocalFree.restype = w.HLOCAL
        self.advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, pointer(w.HANDLE)]
        self.advapi.OpenProcessToken.restype = w.BOOL
        self.advapi.GetTokenInformation.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            w.LPVOID,
            w.DWORD,
            pointer(w.DWORD),
        ]
        self.advapi.GetTokenInformation.restype = w.BOOL
        self.advapi.ConvertSidToStringSidW.argtypes = [w.LPVOID, pointer(w.LPWSTR)]
        self.advapi.ConvertSidToStringSidW.restype = w.BOOL
        self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            w.LPCWSTR,
            w.DWORD,
            pointer(w.LPVOID),
            pointer(w.DWORD),
        ]
        self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
        self.advapi.GetSecurityDescriptorDacl.argtypes = [
            w.LPVOID,
            pointer(w.BOOL),
            pointer(w.LPVOID),
            pointer(w.BOOL),
        ]
        self.advapi.GetSecurityDescriptorDacl.restype = w.BOOL
        self.advapi.GetNamedSecurityInfoW.argtypes = [
            w.LPCWSTR,
            ctypes.c_int,
            w.DWORD,
            pointer(w.LPVOID),
            pointer(w.LPVOID),
            pointer(w.LPVOID),
            pointer(w.LPVOID),
            pointer(w.LPVOID),
        ]
        self.advapi.GetNamedSecurityInfoW.restype = w.DWORD
        self.advapi.SetNamedSecurityInfoW.argtypes = [
            w.LPWSTR,
            ctypes.c_int,
            w.DWORD,
            w.LPVOID,
            w.LPVOID,
            w.LPVOID,
            w.LPVOID,
        ]
        self.advapi.SetNamedSecurityInfoW.restype = w.DWORD

    def current_user_sid(self) -> str:
        w = self.w
        token = w.HANDLE()
        # TOKEN_QUERY; TokenUser. TokenOwner can be Administrators under UAC.
        if not self.advapi.OpenProcessToken(
            self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            length = w.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            if not length.value:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(length.value)
            if not self.advapi.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is a SID pointer.
            sid = ctypes.cast(buffer, ctypes.POINTER(w.LPVOID)).contents
            string = w.LPWSTR()
            if not self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(string)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return string.value
            finally:
                self.kernel.LocalFree(ctypes.cast(string, w.HLOCAL))
        finally:
            self.kernel.CloseHandle(token)

    def _set_dacl(self, path: Path, dacl) -> None:
        if not dacl:
            raise PermissionError("Writer session must have a private access-control list")
        # SE_FILE_OBJECT; DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION.
        error = self.advapi.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None)
        if error:
            raise ctypes.WinError(error)

    def protect_directory(self, session: Path) -> None:
        # No Everyone/Users grants. OI/CI preserve this GUI user's access when
        # the elevated helper atomically replaces a progress/diagnostic file.
        sid = self.current_user_sid()
        sddl = f"D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        descriptor = self.w.LPVOID()
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            present, defaulted = self.w.BOOL(), self.w.BOOL()
            dacl = self.w.LPVOID()
            if (
                not self.advapi.GetSecurityDescriptorDacl(
                    descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
                )
                or not present.value
            ):
                raise PermissionError("Could not configure private writer session permissions")
            self._set_dacl(session, dacl)
        finally:
            self.kernel.LocalFree(descriptor)

    def preserve_user_access(self, session: Path, message: Path) -> None:
        # Copy the GUI-created directory ACL, never derive a fresh ACL from the
        # elevated process's default owner. Do this before publishing the file.
        descriptor, dacl = self.w.LPVOID(), self.w.LPVOID()
        error = self.advapi.GetNamedSecurityInfoW(
            str(session), 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
        )
        if error:
            raise ctypes.WinError(error)
        try:
            self._set_dacl(message, dacl)
        finally:
            self.kernel.LocalFree(descriptor)


def create_session() -> Path:
    session = Path(tempfile.mkdtemp(prefix="autoboot-write-"))
    try:
        if os.name == "nt":
            WindowsSessionPermissions().protect_directory(session)
        return session
    except Exception:
        shutil.rmtree(session, ignore_errors=True)
        raise


def atomic_publish(session: Path, filename: str, payload: dict) -> None:
    if filename not in MESSAGE_FILES:
        raise ValueError("Invalid writer message filename")
    raw = json.dumps(payload).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("Writer status is too large")
    fd, name = tempfile.mkstemp(prefix="status-", suffix=".tmp", dir=session)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        if os.name == "nt":
            WindowsSessionPermissions().preserve_user_access(session, Path(name))
        _replace_with_retry(name, session / filename)
    finally:
        Path(name).unlink(missing_ok=True)


def _read_once(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Unexpected writer status file type: {path}")
    if info.st_size > MAX_MESSAGE_BYTES:
        raise ValueError(f"Writer status file is too large: {path}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MESSAGE_BYTES + 1)
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError(f"Writer status file is too large: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid writer status: {path}")
    return value


def read_message(session: Path, filename: str = "status.json") -> dict:
    if filename not in MESSAGE_FILES:
        raise ValueError("Invalid writer message filename")
    path = session / filename
    deadline = time.monotonic() + READ_RETRY_SECONDS
    delay = 0.005
    while True:
        try:
            return _read_once(path)
        except FileNotFoundError:
            return {}
        except PermissionError as exc:
            # A replacement or scan in progress clears within milliseconds. A
            # real access-control denial persists and is reported explicitly.
            if _transient(exc) and time.monotonic() < deadline:
                time.sleep(delay)
                delay = min(delay * 2, 0.05)
                continue
            if getattr(exc, "winerror", None) in {32, 33}:
                return {}
            raise PermissionError(
                f"Windows denied access to the writer status file: {path}. "
                "AutoBoot could not confirm the writer's result. "
                "The USB must not be treated as verified."
            ) from exc
