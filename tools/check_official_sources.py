"""Read-only live catalogue check; --download additionally verifies complete ISOs.

Run from the project root: python -m tools.check_official_sources [--download]
No storage devices are opened. ISO downloads are scratch test data, not releases.
"""

import argparse
import json
import os
import subprocess
import sys
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
    parser.add_argument("--only", action="append", help="OS key or exact image filename")
    parser.add_argument("--pipeline", action="store_true", help="Full file-backed raw-ISO readback")
    parser.add_argument(
        "--discard", action="store_true", help="Remove only images downloaded by this run"
    )
    args = parser.parse_args()
    findings = []
    for system in CATALOGUE:
        for release in system.releases:
            if args.only and system.key not in args.only and release.filename not in args.only:
                continue
            if not release.download_available:
                continue
            row = {"filename": release.filename, "pinned_sha256": release.sha256}
            try:
                if release.provider == "microsoft" and not release.url:
                    row.update(
                        provider="microsoft",
                        status="Requires Microsoft's current browser-issued ISO link",
                    )
                    findings.append(row)
                    print(json.dumps(row), flush=True)
                    continue
                if release.provider == "apple":
                    from autoboot_usb.services.providers import resolve_apple_installer

                    release = resolve_apple_installer(release)
                    row.update(
                        filename=release.filename,
                        integrity_algorithm=release.verification_algorithm,
                        integrity_digest=release.verification_digest,
                    )
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

                    existed = (Path(args.folder) / release.filename).exists()
                    path, digest = download_image(release, Path(args.folder), progress)
                    row.update(
                        full_download_matches=(
                            True if release.provider == "apple" else digest == release.sha256
                        ),
                        computed_sha256=digest,
                    )
                    if args.pipeline and release.image_type == "hybrid-iso":
                        subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "tools.check_image_pipeline",
                                str(path.parent),
                                "--only",
                                path.name,
                            ],
                            check=True,
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                        )
                        row["file_backed_pipeline_passed"] = True
                    if args.discard and not existed:
                        path.unlink()
                        row["validation_download_removed"] = True
                    print(json.dumps(row), flush=True)
            except Exception as exc:
                row["error"] = str(exc)
                print(json.dumps(row), flush=True)
            findings.append(row)
    report = Path("docs/LIVE_SOURCE_CHECK.json")
    if args.only and report.exists():
        names = {r["filename"] for r in findings}
        previous = json.loads(report.read_text(encoding="utf-8"))
        findings = [r for r in previous if r["filename"] not in names] + findings
    report.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    return int(any("error" in row for row in findings))


if __name__ == "__main__":
    raise SystemExit(main())
