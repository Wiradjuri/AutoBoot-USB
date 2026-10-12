"""Exercise the real browser JavaScript against a local public-form fixture."""

import json
import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-dev-shm-usage"
if sys.platform != "win32" and os.geteuid() == 0:
    os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"  # Only this isolated HTML test.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoboot_usb.ui.microsoft_download import PICK_ISO_SCRIPT, validated_iso_url  # noqa: E402


def main():
    app = QApplication([])
    profile = QWebEngineProfile()
    page = QWebEnginePage(profile)
    live = sys.argv[1:] == ["--live"]
    if live:
        page.loadingChanged.connect(
            lambda request: (
                print(
                    json.dumps(
                        {
                            "browser_status": str(request.status()),
                            "error_code": request.errorCode(),
                            "error": request.errorString(),
                        }
                    ),
                    flush=True,
                )
                if request.errorCode()
                else None
            )
        )
    loaded = []
    page.loadFinished.connect(loaded.append)
    if live:
        page.setUrl(QUrl("https://www.microsoft.com/en-au/software-download/windows11"))
    else:
        page.setHtml("""<section>
          <select id="product"><option value="">Choose</option><option value="3813">Windows 11</option></select>
          <button id="submit-product-edition" onclick="window.productConfirmed=true">Confirm</button>
          </section><section>
          <select id="language"><option value="">Choose</option><option value="english-intl">English International</option></select>
          <button id="submit-sku" onclick="if(window.productConfirmed && document.getElementById('language').value==='english-intl'){
            document.getElementById('links').innerHTML='<a href=&quot;https://software.download.prss.microsoft.com/dbazure/Fixture_x64.iso?t=example&quot;>64-bit Download</a>'}">Confirm</button>
          </section><div id="links"></div>""")
    deadline = time.monotonic() + (120 if live else 10)
    while not loaded and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    urls = []
    while time.monotonic() < deadline and not any(urls):
        page.runJavaScript(
            PICK_ISO_SCRIPT, lambda value: urls.append(value if isinstance(value, str) else "")
        )
        until = time.monotonic() + (1 if live else 0.1)
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(0.005)
    result = next((u for u in urls if u), "")
    if result:
        validated_iso_url(result)
        if live:
            Path("../windows_resolved.json").write_text(json.dumps({"url": result}))
            print("Microsoft live link: resolved", flush=True)
        else:
            assert result.endswith("Fixture_x64.iso?t=example")
            print("Microsoft browser form fixture: PASS", flush=True)
    else:
        print(
            f"Microsoft {'live' if live else 'fixture'} link unresolved; page loaded={loaded}",
            flush=True,
        )
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    profile.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
