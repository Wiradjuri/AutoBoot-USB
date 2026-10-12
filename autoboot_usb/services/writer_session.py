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
MAX_CHANNEL_BYTES = 64 * 1024**2
MESSAGE_FILES = {"status.jsonl", "ready.jsonl", "channel_ack.jsonl"}


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
        # No Everyone/Users grants. The GUI creates every channel before UAC;
        # the elevated helper appends without replacing any of those files.
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
        permissions = WindowsSessionPermissions() if os.name == "nt" else None
        if permissions:
            permissions.protect_directory(session)
        for filename in MESSAGE_FILES:
            path = session / filename
            with path.open("xb"):
                pass
            if permissions:
                permissions.preserve_user_access(session, path)
        return session
    except Exception:
        shutil.rmtree(session, ignore_errors=True)
        raise


def _channel_info(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Unexpected writer status file type: {path}")
    if info.st_size > MAX_CHANNEL_BYTES:
        raise ValueError(f"Writer status channel is too large: {path}")
    return info


def publish_message(session: Path, filename: str, payload: dict) -> None:
    """Append one committed JSON line to the channel created by the GUI.

    Replacing elevated-owned files caused intermittent ERROR_ACCESS_DENIED
    during progress reads. Keeping the original file also keeps its owner,
    integrity label and DACL. A newline commits the message; readers ignore
    an unfinished final line and retain the previous complete message.
    """
    if filename not in MESSAGE_FILES:
        raise ValueError("Invalid writer message filename")
    if not isinstance(payload, dict):
        raise ValueError("Writer message must be an object")
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("Writer status is too large")
    path = session / filename
    info = _channel_info(path)
    if info.st_size + len(raw) + 1 > MAX_CHANNEL_BYTES:
        raise ValueError("Writer status channel is full")
    # No O_CREAT: the elevated helper must never recreate a missing channel.
    deadline = time.monotonic() + 2.0
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        raw += b"\n"
        if os.write(fd, raw) != len(raw):
            raise OSError("Incomplete writer status update")
        os.fsync(fd)
    finally:
        os.close(fd)


def read_message(session: Path, filename: str = "status.jsonl") -> dict:
    if filename not in MESSAGE_FILES:
        raise ValueError("Invalid writer message filename")
    path = session / filename
    deadline = time.monotonic() + 2.0
    while True:
        try:
            return _read_committed_message(path)
        except FileNotFoundError:
            return {}
        except PermissionError as exc:
            # Security software can briefly open a file exclusively. Retry a
            # bounded interval, while a persistent denial still stops the write.
            # Python's CRT file open can report plain EACCES without winerror.
            if time.monotonic() < deadline:
                time.sleep(0.05)
                continue
            raise PermissionError(
                f"Windows denied access to the writer status file: {path}. "
                "AutoBoot could not confirm the writer's result. "
                "The USB must not be treated as verified."
            ) from exc


def _read_committed_message(path: Path) -> dict:
    info = _channel_info(path)
    # Read only the tail, regardless of how long a USB write has been running.
    limit = 2 * (MAX_MESSAGE_BYTES + 1)
    with path.open("rb") as stream:
        stream.seek(max(0, info.st_size - limit))
        raw = stream.read(limit)
    end = raw.rfind(b"\n")
    if end < 0:
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError(f"Writer status is too large: {path}")
        return {}
    start = raw.rfind(b"\n", 0, end) + 1
    line = raw[start:end]
    if len(line) > MAX_MESSAGE_BYTES:
        raise ValueError(f"Writer status is too large: {path}")
    try:
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Invalid writer status: {path}")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid writer status: {path}") from exc
