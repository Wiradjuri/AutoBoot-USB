"""Read vendor-owned installer metadata. No downloads from search-result links."""

from __future__ import annotations

import gzip
import html
import plistlib
import re
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlsplit

from .checksum import check_cancelled

APPLE_CATALOG_SUFFIX = (
    "-15-14-13-12-10.16-10.15-10.14-10.13-10.12-10.11-10.10-10.9-"
    "mountainlion-lion-snowleopard-leopard.merged-1.sucatalog"
)
APPLE_CATALOGS = (
    "https://swscan.apple.com/content/catalogs/others/index-27-26" + APPLE_CATALOG_SUFFIX,
    "https://swscan.apple.com/content/catalogs/others/index-26" + APPLE_CATALOG_SUFFIX,
    "https://swscan.apple.com/content/catalogs/others/index" + APPLE_CATALOG_SUFFIX,
)
APPLE_HOSTS = {"swscan.apple.com", "swcdn.apple.com", "swdist.apple.com", "updates.cdn-apple.com"}


def apple_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in APPLE_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443, 80)
    ):
        raise ValueError("Apple returned an unexpected installer metadata address")
    # Old catalogs contain http links; transport is always upgraded to HTTPS.
    return parsed._replace(scheme="https", netloc=parsed.hostname).geturl()


def _get(url, limit, cancel):
    check_cancelled(cancel)
    with urllib.request.urlopen(apple_url(url), timeout=30) as response:
        apple_url(response.geturl())
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Apple installer metadata exceeds the size limit")
    check_cancelled(cancel)
    return raw


def apple_packages(raw: bytes) -> list[dict]:
    if raw.startswith(b"\x1f\x8b"):
        # Bound decompression as well as the HTTP response size.
        import io

        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(40 * 1024**2 + 1)
    if len(raw) > 40 * 1024**2:
        raise ValueError("Apple installer catalog is too large")
    catalog = plistlib.loads(raw)
    found = []
    for product in catalog.get("Products", {}).values():
        for package in product.get("Packages", []):
            if not package.get("URL", "").endswith("/InstallAssistant.pkg"):
                continue
            digest = package.get("Digest")
            if isinstance(digest, bytes) and len(digest) == 20:
                digest = digest.hex()
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", digest):
                continue
            size = package.get("Size")
            if type(size) is not int or size < 100 * 1024**2:
                continue
            distributions = product.get("Distributions", {})
            distribution = distributions.get("English") or distributions.get("en")
            if not distribution:
                continue
            found.append(
                {
                    "url": apple_url(package["URL"]),
                    "digest": digest.lower(),
                    "size": size,
                    "distribution": apple_url(distribution),
                    "date": product.get("PostDate", datetime.min),
                }
            )
    return sorted(found, key=lambda item: item["date"], reverse=True)


def resolve_apple_installer(release, cancel=None):
    failures = []
    for catalog_url in APPLE_CATALOGS:
        try:
            packages = apple_packages(_get(catalog_url, 40 * 1024**2, cancel))
            if not packages:
                raise ValueError("Apple catalog has no full installer packages")
            candidates = []
            for package in packages[:8]:
                document = _get(package["distribution"], 2 * 1024**2, cancel).decode("utf-8")
                version = re.search(
                    r"<key>(?:macOSProductVersion|VERSION)</key>\s*<string>([0-9.]+)</string>",
                    document,
                )
                title = re.search(r"<title>(.*?)</title>", document, re.S)
                if not version or (title and "beta" in title[1].lower()):
                    continue
                candidates.append(
                    (
                        tuple(int(p) for p in version[1].split(".")),
                        version[1],
                        html.unescape(title[1]) if title else "macOS",
                        package,
                    )
                )
            if not candidates:
                raise ValueError("Apple did not identify a stable full installer")
            _, version, name, package = max(candidates, key=lambda value: value[0])
            return replace(
                release,
                name=name,
                version=version,
                edition="Apple full installer",
                filename=f"macOS-{version}-InstallAssistant.pkg",
                url=package["url"],
                size_bytes=package["size"],
                verification_algorithm="sha1",
                verification_digest=package["digest"],
                checksum_url=catalog_url,
            )
        except urllib.error.HTTPError as exc:
            if exc.code not in {404, 410}:
                raise
            failures.append(f"catalog unavailable ({exc.code})")
    raise RuntimeError(
        "Apple's full-installer catalogs are currently unavailable. " + "; ".join(failures)
    )
