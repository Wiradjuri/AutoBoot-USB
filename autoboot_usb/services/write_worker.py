"""Unprivileged Qt worker controlling a UAC-elevated helper via bounded JSON."""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import re
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..models import ImageSelection, UsbDevice
from .writer import trusted_release
from .writer_session import atomic_publish, create_session, read_message

LOG = logging.getLogger("autoboot_usb.writer")


def helper_command(session: Path, digest: str) -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        helper = (
            Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "AutoBootUSBWriter.exe"
        )
        if not helper.is_file():
            raise FileNotFoundError("The packaged writer helper is missing. Reinstall AutoBoot USB")
        return str(helper), [str(session / "request.json"), digest]
    helper = Path(__file__).resolve().parents[2] / "run_writer.py"
    if not helper.is_file():
        raise FileNotFoundError(
            "The USB writer entry point is missing. Extract the complete source"
        )
    # Windows GUI builds should run the helper without a background console.
    # The actual confirmation has its own foreground/topmost MessageBoxW.
    python = Path(sys.executable)
    # Windows venv executables are redirectors, not the actual interpreter.
    # The helper needs only stdlib, so elevate the underlying interpreter
    # directly and avoid an extra launcher/process and its startup hooks.
    if sys.platform == "win32" and sys.prefix != sys.base_prefix:
        base = Path(getattr(sys, "_base_executable", "") or sys.executable)
        if base.is_file():
            python = base
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        windowless = python.with_name("pythonw.exe")
        if windowless.is_file():
            python = windowless
    return str(python), ["-I", "-S", str(helper), str(session / "request.json"), digest]


def launch_elevated(executable: str, args: list[str]):
    from ctypes import wintypes as w

    class ExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", w.DWORD),
            ("fMask", w.ULONG),
            ("hwnd", w.HWND),
            ("lpVerb", w.LPCWSTR),
            ("lpFile", w.LPCWSTR),
            ("lpParameters", w.LPCWSTR),
            ("lpDirectory", w.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", w.HINSTANCE),
            ("lpIDList", w.LPVOID),
            ("lpClass", w.LPCWSTR),
            ("hkeyClass", w.HKEY),
            ("dwHotKey", w.DWORD),
            ("hIcon", w.HANDLE),
            ("hProcess", w.HANDLE),
        ]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.IsUserAnAdmin.argtypes = []
    shell.IsUserAnAdmin.restype = w.BOOL
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype = w.BOOL
    info = ExecuteInfo()
    # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC: the caller is a QThread
    # without a Windows message loop; finish process activation before return.
    info.cbSize, info.fMask = ctypes.sizeof(info), 0x140
    # Request UAC automatically only when this process lacks administrator
    # access. Starting AutoBoot from an already-elevated process needs no prompt.
    info.lpVerb = "open" if shell.IsUserAnAdmin() else "runas"
    info.lpFile, info.lpParameters, info.lpDirectory = (
        executable,
        subprocess.list2cmdline(args),
        str(Path(executable).parent),
    )
    # SW_SHOW: don't suppress the elevated helper's own confirmation window.
    # In source mode pythonw.exe avoids an unwanted console window.
    info.nShow = 5
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise PermissionError("Administrator approval was cancelled. No image was written")
        raise OSError(error, ctypes.FormatError(error))
    if not info.hProcess:
        raise OSError("Windows did not return a handle for the writer helper")
    return info.hProcess


def _read_status(session: Path, filename: str = "status.json") -> dict:
    return read_message(session, filename)


def process_api():
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = (
        [w.HANDLE, w.DWORD],
        w.DWORD,
    )
    kernel.GetExitCodeProcess.argtypes, kernel.GetExitCodeProcess.restype = (
        [w.HANDLE, ctypes.POINTER(w.DWORD)],
        w.BOOL,
    )
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    return kernel


def check_helper_startup(executable: str, args: list[str], session: Path) -> None:
    """Run the exact helper without UAC or disk access before requesting elevation."""
    # Both packaged and source commands end with request path and digest.
    command = [executable, *args[:-2], "--check", *args[-2:]]
    result = subprocess.run(
        command,
        cwd=str(Path(executable).parent),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0 or _read_status(session, "ready.json").get("state") != "ready":
        status = _read_status(session)
        detail = status.get("message") or result.stderr.strip()[-2000:] or "No startup status"
        raise RuntimeError(
            f"USB writer startup check failed (exit code {result.returncode}): {detail}. "
            f"No image was written. Diagnostics: {session}"
        )


class UsbWriteWorker(QThread):
    progress = Signal(str, object, object)
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(self, image: ImageSelection, target: UsbDevice, parent=None):
        super().__init__(parent)
        self.image, self.target = image, target
        self.stop_event = threading.Event()
        self.session: Path | None = None

    def cancel(self):
        self.stop_event.set()

    def run(self):
        handle = None
        kernel = None
        try:
            if sys.platform != "win32":
                raise RuntimeError(
                    "Physical writing is currently available only in the Windows preview"
                )
            if not self.image.verified:
                raise ValueError("A checksum-verified image is required")
            trusted_release(self.image.trusted_sha256)
            self.session = create_session()
            payload = {
                "image": str(self.image.path.resolve()),
                "image_size": self.image.path.stat().st_size,
                "sha256": self.image.trusted_sha256,
                "target": asdict(self.target),
                "confirmed": True,
                "experimental": True,
            }
            raw = json.dumps(payload, sort_keys=True).encode("utf-8")
            (self.session / "request.json").write_bytes(raw)
            executable, args = helper_command(self.session, hashlib.sha256(raw).hexdigest())
            LOG.info(
                "Confirmed writer request for %s; recovery state: %s",
                self.target.device_id,
                self.session,
            )
            if self.stop_event.is_set():
                self.cancelled.emit("Cancelled before writing. No image was written")
                return
            self.progress.emit("starting_helper", 0, 0)
            check_helper_startup(executable, args, self.session)
            if self.stop_event.is_set():
                self.cancelled.emit("Cancelled before administrator approval. No image was written")
                return
            from ctypes import wintypes as w

            kernel = process_api()
            self.progress.emit("awaiting_admin", 0, 0)
            handle = launch_elevated(executable, args)
            # Windows has launched the child. The nonce round trip below also
            # confirms the GUI can read statuses created with its elevated token.
            self.progress.emit("admin_approved", 0, 0)
            previous = None
            communication_error = None
            while True:
                status = {}
                if communication_error is None:
                    try:
                        status = _read_status(self.session)
                        nonce = status.get("nonce")
                        if (
                            status.get("state") == "admin_approved"
                            and isinstance(nonce, str)
                            and re.fullmatch(r"[0-9a-f]{32}", nonce)
                            and status != previous
                        ):
                            atomic_publish(self.session, "channel_ack.json", {"nonce": nonce})
                    except (OSError, ValueError) as exc:
                        communication_error = exc
                if self.stop_event.is_set() or communication_error:
                    try:
                        (self.session / "cancel").touch(exist_ok=True)
                    except OSError as exc:
                        communication_error = communication_error or exc
                if status != previous:
                    phase, count, total = (
                        status.get("state"),
                        status.get("count"),
                        status.get("total"),
                    )
                    if (
                        isinstance(phase, str)
                        and phase
                        in {
                            "admin_approved",
                            "confirming_usb",
                            "checking",
                            "preparing",
                            "writing",
                            "flushing",
                            "verifying",
                        }
                        and type(count) is int
                        and type(total) is int
                        and 0 <= count <= total
                    ):
                        self.progress.emit(phase, count, total)
                    previous = status
                waited = kernel.WaitForSingleObject(handle, 200)
                if waited == 0:
                    break
                if waited != 258:
                    raise OSError("Could not monitor the elevated writer process")
            code = w.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError("Could not determine writer completion")
            # Keep the GUI busy until the actual child exits even after an IPC
            # fault, so another write cannot overlap a still-stopping helper.
            if communication_error:
                raise RuntimeError(f"{communication_error} Diagnostics: {self.session}")
            result = _read_status(self.session)
            if (
                code.value == 0
                and result.get("state") == "complete"
                and result.get("sha256") == self.image.trusted_sha256
                and result.get("target") == self.target.device_id
                and result.get("count") == payload["image_size"]
                and result.get("total") == payload["image_size"]
            ):
                self.completed.emit(result["sha256"])
            elif result.get("state") == "cancelled":
                self.cancelled.emit(str(result.get("message", "USB creation cancelled")))
            else:
                if not result:
                    raise RuntimeError(
                        "The elevated writer exited without reporting its status "
                        f"(Windows exit code {code.value}). No verified USB was created. "
                        "Check whether Windows blocked the helper or whether it could "
                        f"access the confirmation request. Diagnostics: {self.session}"
                    )
                raise RuntimeError(
                    str(
                        result.get(
                            "message", "Writer stopped unexpectedly. Recreate the USB before use"
                        )
                    )
                    + f" Diagnostics: {self.session}"
                )
        except Exception as exc:
            if handle and self.session:
                # A diagnostic/IPC error must not leave an unacknowledged
                # helper waiting or allow a later confirmation to start a write.
                try:
                    (self.session / "cancel").touch(exist_ok=True)
                except OSError:
                    LOG.exception("Could not signal cancellation to the writer")
            LOG.exception("USB write failed")
            self.failed.emit(str(exc))
        finally:
            if handle and kernel:
                kernel.CloseHandle(handle)
            # Keep bounded request/status files for diagnosis, never resume a raw write.
