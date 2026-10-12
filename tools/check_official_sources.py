"""Read-only live catalogue check; --download additionally verifies complete ISOs.

Run from the project root: python -m tools.check_official_sources [--download]
No storage devices are opened. ISO downloads are scratch test data, not releases.
"""

import argparse
import json
import time
from pathlib import Path

from autoboot_usb.catalogue import CATALOGUE
from autoboot_usb.services.download import (
    _check_response,
    _request,
    check_upstream_checksum,
    download_image,
    https_opener,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--folder", default=".validation-downloads")
    args = parser.parse_args()
    findings = []
    for system in CATALOGUE:
        for release in system.releases:
            if not release.download_available:
                continue
            row = {"filename": release.filename, "pinned_sha256": release.sha256}
            try:
                check_upstream_checksum(release)
                with https_opener().open(_request(release.url, "HEAD"), timeout=35) as response:
                    _check_response(response)
                    size = int(response.headers.get("Content-Length") or 0)
                    if release.size_bytes and size != release.size_bytes:
                        raise ValueError("Unexpected Content-Length")
                    row.update(
                        head_status=response.status,
                        size_bytes=size,
                        final_url=response.geturl(),
                        manifest_matches=True,
                    )
                print(json.dumps(row), flush=True)
                if args.download:
                    last = [0.0]

                    def progress(n, total, speed):
                        now = time.monotonic()
                        if now - last[0] >= 30 or n == total:
                            print(
                                json.dumps(
                                    {"file": release.filename, "downloaded": n, "total": total}
                                ),
                                flush=True,
                            )
                            last[0] = now

                    path, digest = download_image(release, Path(args.folder), progress)
                    row.update(full_download_matches=digest == release.sha256, saved_path=str(path))
                    print(json.dumps(row), flush=True)
            except Exception as exc:
                row["error"] = str(exc)
                print(json.dumps(row), flush=True)
            findings.append(row)
    report = Path("docs/LIVE_SOURCE_CHECK.json")
    report.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    return int(any("error" in row for row in findings))


if __name__ == "__main__":
    raise SystemExit(main())
