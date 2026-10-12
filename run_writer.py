"""Qt-free helper bootstrap, including diagnostics before application imports."""

import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

# -I -S excludes environment/site hooks. Only restore this application's root.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _bound_session(args: list[str]) -> Path:
    """Allow bootstrap diagnostics only beside a valid, hash-bound request."""
    if len(args) != 2 or not re.fullmatch(r"[0-9a-f]{64}", args[1]):
        raise ValueError("Invalid writer arguments")
    path = Path(args[0])
    if not path.is_absolute() or path.name != "request.json":
        raise ValueError("Invalid writer request path")
    for candidate in (path, path.parent):
        attributes = getattr(candidate.lstat(), "st_file_attributes", 0)
        if candidate.is_symlink() or attributes & 0x400:
            raise ValueError("Writer request cannot use reparse points")
    session = path.resolve().parent
    if (
        session.parent != Path(tempfile.gettempdir()).resolve()
        or not session.name.startswith("autoboot-write-")
        or path.stat().st_size > 16384
    ):
        raise ValueError("Invalid writer request location or size")
    if hashlib.sha256(path.read_bytes()).hexdigest() != args[1]:
        raise ValueError("Writer request changed after confirmation")
    return session


def _publish(session: Path, filename: str, payload: dict) -> None:
    fd, name = tempfile.mkstemp(prefix="bootstrap-", suffix=".tmp", dir=session)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, session / filename)
    finally:
        Path(name).unlink(missing_ok=True)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check_only = args[:1] == ["--check"]
    if check_only:
        args = args[1:]
    try:
        session = _bound_session(args)
    except (OSError, ValueError):
        # Never write diagnostics to an unvalidated caller-supplied directory.
        return 2
    try:
        from autoboot_usb.services.elevated_writer import main as write_main
        from autoboot_usb.services.elevated_writer import read_request

        if check_only:
            # No elevation, confirmation, disk opening or write on this path.
            read_request(Path(args[0]), args[1])
            if sys.platform == "win32":
                from autoboot_usb.adapters.windows_writer import WinApi

                WinApi()  # Bind Win32 functions; does not open any device.
            _publish(session, "ready.json", {"state": "ready"})
            return 0
        return write_main(args)
    except Exception as exc:
        # pythonw.exe has no stderr console. Preserve import/startup failures
        # in the same bounded status protocol that the GUI already monitors.
        _publish(
            session,
            "status.json",
            {
                "state": "failed",
                "message": f"Writer startup failed: {type(exc).__name__}: {exc}"[:1000],
                "traceback": traceback.format_exc()[-1500:],
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
