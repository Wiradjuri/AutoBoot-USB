"""Unprivileged Qt worker controlling a UAC-elevated helper via bounded JSON."""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..models import ImageSelection, UsbDevice
from .writer import trusted_release

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
    # Windows GUI builds should run the helper without a background console.
    # The actual confirmation has its own foreground/topmost MessageBoxW.
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        windowless = python.with_name("pythonw.exe")
        if windowless.is_file():
            python = windowless
    return str(python), ["-I", str(helper), str(session / "request.json"), digest]


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
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype = w.BOOL
    info = ExecuteInfo()
    info.cbSize, info.fMask, info.lpVerb = ctypes.sizeof(info), 0x40, "runas"
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


def _read_status(session: Path) -> dict:
    try:
        path = session / "status.json"
        if path.stat().st_size > 16384:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


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
            self.session = Path(tempfile.mkdtemp(prefix="autoboot-write-"))
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
            handle = launch_elevated(executable, args)
            # ShellExecuteExW's `runas` returns only after Windows has launched
            # the elevated child, so the UAC step is no longer pending here.
            self.progress.emit("admin_approved", 0, 0)
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
            kernel.CloseHandle.argtypes = [w.HANDLE]
            previous = None
            while True:
                if self.stop_event.is_set():
                    (self.session / "cancel").touch(exist_ok=True)
                status = _read_status(self.session)
                if status != previous:
                    phase, count, total = (
                        status.get("state"),
                        status.get("count"),
                        status.get("total"),
                    )
                    if (
                        isinstance(phase, str)
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
                        "access the confirmation request."
                    )
                raise RuntimeError(
                    str(
                        result.get(
                            "message", "Writer stopped unexpectedly. Recreate the USB before use"
                        )
                    )
                )
        except Exception as exc:
            LOG.exception("USB write failed")
            self.failed.emit(str(exc))
        finally:
            if handle and kernel:
                kernel.CloseHandle(handle)
            # Keep bounded request/status files for diagnosis, never resume a raw write.
