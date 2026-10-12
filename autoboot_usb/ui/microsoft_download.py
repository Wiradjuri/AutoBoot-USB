"""Obtain expiring Microsoft ISO links through its normal browser download flow."""

import json
import re
import time
from dataclasses import replace
from urllib.parse import urlsplit

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

MICROSOFT_ISO_HOSTS = {
    "software.download.prss.microsoft.com",
    "download.microsoft.com",
    "software-download.microsoft.com",
    "download.windowsupdate.com",
}


def validated_iso_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in MICROSOFT_ISO_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not parsed.path.lower().endswith(".iso")
    ):
        raise ValueError("Microsoft returned an unexpected installer download address")
    return url


# Only select the public ISO edition/language and press their Confirm buttons.
# Never sign in, accept a licence, bypass a challenge, or submit account data.
_PICK_ISO_TEMPLATE = r"""(() => {
 const state = window.__autobootDownload || (window.__autobootDownload = {});
 const wanted = __AUTOBOOT_LANGUAGE__;
 const names = (wanted === 'English (United States)' ? [wanted, 'English'] : [wanted])
   .map(s => s.toLowerCase());
 const matches = o => names.includes(o.text.trim().toLowerCase());
 const all = Array.from(document.querySelectorAll('select'));
 const product = all.find(s => Array.from(s.options).some(o => /Windows (10|11)/i.test(o.text)));
 const language = all.find(s => Array.from(s.options).some(matches));
 const confirm = (select, ids) => {
   let button = ids.map(id => document.getElementById(id)).find(Boolean);
   if (!button) {
     const area = select.closest('section') || select.closest('[class*=row]') || select.parentElement.parentElement;
     button = Array.from(area.querySelectorAll('button,input[type=button],a[role=button]'))
       .find(b => /^Confirm$/i.test((b.innerText || b.value || '').trim()));
   }
   if (button && !button.disabled && button.getAttribute('aria-disabled') !== 'true' &&
       !button.classList.contains('disabled')) {
     button.click();
     return true;
   }
   return false;
 };
 if (product && !state.product) {
   const option = Array.from(product.options).find(o => /Windows (10|11)/i.test(o.text) && /[0-9]/.test(o.value));
   if (option) {
     product.value = option.value;
     product.dispatchEvent(new Event('change', {bubbles:true}));
     state.product = confirm(product, ['submit-product-edition','submit-product-edition-btn']);
   }
 }
 if (language && !state.language) {
   const option = Array.from(language.options).find(matches);
   if (option) {
     language.value = option.value;
     language.dispatchEvent(new Event('change', {bubbles:true}));
     state.language = confirm(language, ['submit-sku','submit-sku-btn']);
   }
 }
 const links = Array.from(document.querySelectorAll('a[href]'));
 const iso = links.find(a => /\.iso(?:\?|$)/i.test(a.href) &&
   (/64.bit/i.test(a.innerText) || /_x64[._]/i.test(a.href)));
 return iso ? iso.href : '';
})()"""


def iso_picker_script(language: str) -> str:
    if language not in {"English International", "English (United States)"}:
        raise ValueError("Unsupported pinned Microsoft installer language")
    return _PICK_ISO_TEMPLATE.replace("__AUTOBOOT_LANGUAGE__", json.dumps(language))


PICK_ISO_SCRIPT = iso_picker_script("English International")


class MicrosoftDownloadDialog(QDialog):
    resolved = Signal(object)

    def __init__(self, system_key, release, parent=None):
        super().__init__(parent)
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.release = release
        self.language = release.download_language or "English International"
        self.picker_script = iso_picker_script(self.language)
        self.started = time.monotonic()
        self.setWindowTitle(f"Download {release.name} from Microsoft")
        self.setModal(True)
        self.resize(850, 650)
        layout = QVBoxLayout(self)
        self.status = QLabel(
            f"Getting the {self.language}, 64-bit installer from Microsoft. "
            "The download will be saved and checked automatically."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpUserAgent(
            re.sub(r"QtWebEngine/\S+\s*", "", self.profile.httpUserAgent())
        )
        # The Windows 10 ISO page is intended for platforms without Microsoft's
        # media-creation tool. Request that public ISO view rather than the EXE.
        if system_key == "win10":
            self.profile.setHttpUserAgent(
                re.sub(r"\([^)]*\)", "(X11; Linux x86_64)", self.profile.httpUserAgent(), count=1)
            )
        self.page = QWebEnginePage(self.profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        layout.addWidget(self.view, 1)
        cancel = QPushButton("Cancel")
        retry = QPushButton("Retry Microsoft download page")
        retry.clicked.connect(self._retry)
        layout.addWidget(retry)
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self.timer = QTimer(self)
        self.timer.setInterval(1200)
        self.timer.timeout.connect(self._poll)
        self.finished.connect(self.timer.stop)
        self.finished.connect(self._dispose_browser)
        self.profile.downloadRequested.connect(self._download_requested)
        self.view.loadFinished.connect(self._loaded)
        self.view.setUrl(QUrl(release.checksum_url))

    def _dispose_browser(self):
        # A WebEngine profile must outlive every page using it.
        self.view.deleteLater()
        self.page.deleteLater()
        self.profile.deleteLater()

    def _retry(self):
        self.started = time.monotonic()
        self.view.reload()
        self.status.setText(f"Getting the {self.language}, 64-bit installer from Microsoft…")

    def _loaded(self, ok):
        if ok:
            self.timer.start()
        else:
            self.status.setText(
                "Microsoft's download page could not load. Check your connection or retry later."
            )

    def _poll(self):
        if time.monotonic() - self.started > 90:
            self.timer.stop()
            self.status.setText(
                "Microsoft did not provide an ISO link. Retry this page, or cancel and import "
                "the matching official Windows ISO. No image has been downloaded."
            )
            return
        host = self.page.url().host().lower()
        if host != "www.microsoft.com":
            return
        self.page.runJavaScript(self.picker_script, self._url_ready)

    def _download_requested(self, request):
        url = request.url().toString()
        request.cancel()  # The application downloader checks the pinned hash.
        self._url_ready(url)

    def _url_ready(self, value):
        if not isinstance(value, str) or not value or not self.isVisible():
            return
        try:
            url = validated_iso_url(value)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.timer.stop()
        self.resolved.emit(replace(self.release, url=url))
        self.accept()
