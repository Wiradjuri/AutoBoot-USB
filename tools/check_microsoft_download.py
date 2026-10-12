"""Read-only live Microsoft browser/download check on a Windows CI runner."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QNetworkProxyFactory
from PySide6.QtWidgets import QApplication

from autoboot_usb.catalogue import BY_KEY
from autoboot_usb.services.download import download_image
from autoboot_usb.ui.microsoft_download import MicrosoftDownloadDialog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("system", choices=("win10", "win11"))
    args = parser.parse_args()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QNetworkProxyFactory.setUseSystemConfiguration(True)
    app = QApplication([])
    release = BY_KEY[args.system].releases[0]
    dialog = MicrosoftDownloadDialog(args.system, release)
    found = []
    dialog.resolved.connect(found.append)
    dialog.open()
    report = {"system": args.system, "filename": release.filename, "physical_disk_accessed": False}
    try:
        deadline = time.monotonic() + 100
        while not found and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        if not found:
            details = []
            dialog.page.runJavaScript(
                "JSON.stringify({host:location.hostname,text:document.body.innerText.slice(0,16000),"
                "state:window.__autobootDownload,selects:Array.from(document.querySelectorAll('select'))"
                ".map(s=>({id:s.id,value:s.value,options:Array.from(s.options)"
                ".map(o=>({value:o.value,text:o.text}))}))})",
                details.append,
            )
            detail_deadline = time.monotonic() + 3
            while not details and time.monotonic() < detail_deadline:
                app.processEvents()
                time.sleep(0.02)
            if details and isinstance(details[0], str):
                report["public_page"] = json.loads(details[0])
            raise RuntimeError(dialog.status.text())
        report["browser_link_resolved"] = True
        with tempfile.TemporaryDirectory(prefix="autoboot-ms-check-") as folder:
            last = [0.0]

            def progress(n, total, _speed):
                if time.monotonic() - last[0] > 30 or n == total:
                    print(json.dumps({"downloaded": n, "total": total}), flush=True)
                    last[0] = time.monotonic()

            path, digest = download_image(found[0], Path(folder), progress)
            report.update(full_download_verified=True, sha256=digest, bytes=path.stat().st_size)
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        dialog.reject()
        app.processEvents()
        Path("vendor-check.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    return int("error" in report)


if __name__ == "__main__":
    raise SystemExit(main())
