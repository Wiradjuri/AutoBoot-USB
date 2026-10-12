"""Trusted Windows system tools and bounded, cancellable process execution."""

import ctypes
import json
import subprocess
import tempfile
import time
from pathlib import Path

from ..services.checksum import check_cancelled


def system_binary(relative: str) -> str:
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [w.LPWSTR, w.UINT]
    kernel.GetSystemDirectoryW.restype = w.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    count = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    path = Path(buffer.value) / relative
    if not path.is_file():
        raise FileNotFoundError(f"Required Windows component is missing: {path}")
    return str(path)


def run_command(command, cancel=None, payload=None, *, cancellable=True):
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            if payload is not None:
                process.stdin.write(json.dumps(payload).encode("utf-8"))
                process.stdin.close()
            while process.poll() is None:
                if cancellable:
                    check_cancelled(cancel)
                time.sleep(0.1)
            output.seek(0)
            raw = output.read(2 * 1024**2).decode("utf-8-sig", "replace")
            if process.returncode:
                raise RuntimeError(
                    raw.strip()[-3000:] or f"Windows tool exited {process.returncode}"
                )
            return raw
        except BaseException:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            raise


def powershell(script, payload, cancel=None, *, cancellable=True):
    # Request values are JSON data on stdin, never interpolated into shell code.
    prefix = (
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding="
        "[Text.UTF8Encoding]::new($false); "
        "$r=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
    )
    return run_command(
        [
            system_binary("WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            prefix + script,
        ],
        cancel,
        payload,
        cancellable=cancellable,
    )
