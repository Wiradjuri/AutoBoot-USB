"""Opt-in packaged-runtime checks: no elevation, disk enumeration or disk writes."""

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch


def run(app):
    from PySide6.QtCore import QUrl
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from .catalogue import BY_KEY
    from .models import Release, UsbDevice
    from .services.download import download_image
    from .services.write_worker import check_helper_startup, helper_command
    from .services.writer_session import create_session, read_message
    from .ui.icons import product_icon
    from .ui.window import AutoBootWindow

    class Adapter:
        def list_usb_disks(self):
            return []

        def firmware_mode(self):
            return "UEFI"

        def boot_guidance(self):
            return "Packaging check"

    def wait_until(predicate):
        deadline = time.monotonic() + 30
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError("Packaged Qt runtime did not complete its check")
            app.processEvents()
            time.sleep(0.01)

    with patch("autoboot_usb.ui.window.get_adapter", return_value=Adapter()):
        window = AutoBootWindow(auto_download=False)
        window.show()
        wait_until(lambda: window.scanner is None)
        if window.create_button.isEnabled() or product_icon().isNull():
            raise RuntimeError("Unsafe initial GUI state or missing icon")
        window._toggle_theme()
        window.close()

    # Uses the real Chromium child process, resources and locales; no vendor traffic.
    browser = QWebEngineView()
    loaded = []
    browser.loadFinished.connect(loaded.append)
    browser.setHtml("<html><body>AutoBoot package check</body></html>", QUrl("about:blank"))
    wait_until(lambda: bool(loaded))
    if not loaded[0]:
        raise RuntimeError("Bundled WebEngine failed to load a local page")
    result = []
    browser.page().runJavaScript("document.body.innerText", result.append)
    wait_until(lambda: bool(result))
    if result[0] != "AutoBoot package check":
        raise RuntimeError("Bundled WebEngine JavaScript failed")
    browser.close()
    browser.deleteLater()
    app.processEvents()

    with tempfile.TemporaryDirectory() as folder:
        payload = b"AutoBoot packaging cache check\x00" * 1024
        digest = hashlib.sha256(payload).hexdigest()
        release = Release(
            "Check",
            "1",
            "Installer",
            "x86-64",
            "check.iso",
            "https://cdimage.kali.org/check.iso",
            digest,
            1024,
            checksum_url="https://cdimage.kali.org/SHA256SUMS",
            size_bytes=len(payload),
        )
        cached = Path(folder) / release.filename
        cached.write_bytes(payload)
        download_image(release, Path(folder))
        if cached.read_bytes() != payload:
            raise RuntimeError("Bundled download cache verification failed")

    session = create_session()
    try:
        data = {
            "image": str(session / "nonexistent.iso"),
            "image_size": 1024,
            "sha256": BY_KEY["kali"].releases[0].sha256,
            "confirmed": True,
            "target": asdict(
                UsbDevice(
                    "PhysicalDrive2",
                    "Package check only",
                    16_000_000_000,
                    "usb",
                    True,
                    False,
                    serial="PACKAGING-CHECK",
                )
            ),
        }
        raw = json.dumps(data).encode()
        (session / "request.json").write_bytes(raw)
        executable, args = helper_command(session, hashlib.sha256(raw).hexdigest())
        check_helper_startup(executable, args, session)
        if read_message(session, "ready.jsonl") != {"state": "ready"}:
            raise RuntimeError("Bundled helper did not report ready")
        # Only --check is permitted here. Invalid hash must fail before any device access.
        rejected = subprocess.run(
            [executable, "--check", args[0], "0" * 64],
            timeout=30,
            check=False,
        )
        if rejected.returncode != 2 or Path(data["image"]).exists():
            raise RuntimeError("Bundled helper failed the request rejection check")
    finally:
        shutil.rmtree(session)
    return {
        "status": "pass",
        "gui": "pass",
        "icons": "pass",
        "webengine": "pass",
        "download_cache_sha256": "pass",
        "writer_startup_and_rejection": "pass",
        "physical_usb_and_firmware_boot": "not tested",
        "signed": False,
    }
