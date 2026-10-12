"""Ask UAC for read-only enumeration only when Windows denies the normal scan."""

import ctypes
import hashlib
import json
import re

from ..models import UsbDevice
from .checksum import OperationCancelled
from .write_worker import check_helper_startup, helper_command, launch_elevated, process_api
from .writer_session import create_session, publish_message, read_message


def discover_with_admin(cancel=None):
    from ctypes import wintypes as w

    session = create_session()
    raw = json.dumps({"operation": "discover"}).encode()
    (session / "request.json").write_bytes(raw)
    executable, args = helper_command(session, hashlib.sha256(raw).hexdigest())
    args.insert(len(args) - 2, "--discover")
    check_helper_startup(executable, args, session)
    if cancel and cancel():
        raise OperationCancelled()
    kernel = process_api()
    handle = launch_elevated(executable, args)
    fault = None
    try:
        while True:
            try:
                status = read_message(session)
                if status.get("state") == "admin_approved" and re.fullmatch(
                    r"[0-9a-f]{32}", str(status.get("nonce", ""))
                ):
                    publish_message(session, "channel_ack.jsonl", {"nonce": status["nonce"]})
            except (OSError, ValueError) as exc:
                fault = fault or exc
            if fault or (cancel and cancel()):
                (session / "cancel").touch(exist_ok=True)
            result = kernel.WaitForSingleObject(handle, 200)
            if result == 0:
                break
            if result != 258:
                raise OSError("Could not monitor administrator USB discovery")
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError("Could not read administrator discovery result")
        if fault:
            raise fault
        if cancel and cancel():
            raise OperationCancelled()
        status = read_message(session)
        if (
            code.value
            or status.get("state") != "complete"
            or not isinstance(status.get("devices"), list)
        ):
            raise RuntimeError(status.get("message", "Administrator USB discovery failed"))
        return [UsbDevice(**item) for item in status["devices"]]
    finally:
        kernel.CloseHandle(handle)
