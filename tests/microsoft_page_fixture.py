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

from autoboot_usb.ui.microsoft_download import (  # noqa: E402
    PICK_ISO_SCRIPT,
    iso_picker_script,
    validated_iso_url,
)


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
    fixture = """<section>
      <select id="product"><option value="">Choose</option><option value="3813">Windows 11</option></select>
      <button id="submit-product-edition" onclick="window.productConfirmed=true">Confirm</button>
      </section><section>
      <select id="language"><option value="">Choose</option><option value="english-intl">English International</option></select>
      <button id="submit-sku" onclick="if(window.productConfirmed && document.getElementById('language').value==='english-intl'){
        document.getElementById('links').innerHTML='<a href=&quot;https://software.download.prss.microsoft.com/dbazure/Fixture_x64.iso?t=example&quot;>64-bit Download</a>'}">Confirm</button>
      </section><div id="links"></div>"""
    page.loadFinished.connect(loaded.append)
    if live:
        page.setUrl(QUrl("https://www.microsoft.com/en-au/software-download/windows11"))
    else:
        page.setHtml(fixture)
    deadline = time.monotonic() + (120 if live else 10)
    while not loaded and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    urls = []
    while time.monotonic() < deadline and not any(urls):
        page.runJavaScript(
            iso_picker_script("English (United States)") if live else PICK_ISO_SCRIPT,
            lambda value: urls.append(value if isinstance(value, str) else ""),
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
            # The live Windows 11 form omits English International. Verify that
            # the release's English US selection still submits the normal form.
            loaded.clear()
            urls.clear()
            page.setHtml(
                fixture.replace("English International", "English (United States)")
                .replace("english-intl", "english-us")
            )
            deadline = time.monotonic() + 8
            while not loaded and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            while not any(urls) and time.monotonic() < deadline:
                page.runJavaScript(iso_picker_script("English (United States)"), urls.append)
                until = time.monotonic() + 0.1
                while time.monotonic() < until:
                    app.processEvents()
                    time.sleep(0.005)
            assert any(u == result for u in urls), "English US-only form was not submitted"
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
