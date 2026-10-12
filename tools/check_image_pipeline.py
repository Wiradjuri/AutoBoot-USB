"""Validate real ISO -> file-backed disk -> read-back. Never opens physical disks."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Slot

from autoboot_usb.catalogue import CATALOGUE
from autoboot_usb.services.download import ImageVerifier
from autoboot_usb.services.writer import copy_and_verify, validate_hybrid_header


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image_folder", type=Path)
    parser.add_argument(
        "--only", help="Check a single filename; retain earlier findings for other files"
    )
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    results = []
    for system in CATALOGUE:
        for release in system.releases:
            if args.only and release.filename != args.only:
                continue
            path = args.image_folder / release.filename
            if not path.is_file():
                continue
            counters, hashes, errors = [], [], []

            class Receiver(QObject):
                @Slot(object, object)
                def progress(self, count, total):
                    counters.append((count, total))

                @Slot(str)
                def complete(self, digest):
                    hashes.append(digest)

                @Slot(str)
                def failed(self, message):
                    errors.append(message)

            receiver = Receiver()
            worker = ImageVerifier(path)
            worker.progress.connect(receiver.progress)
            worker.completed.connect(receiver.complete)
            worker.failed.connect(receiver.failed)
            worker.start()
            while worker.isRunning():
                app.processEvents()
                worker.wait(10)
            app.processEvents()
            if errors or hashes != [release.sha256]:
                raise AssertionError(errors or "Qt checksum verification failed")
            if not all(0 <= n <= total for n, total in counters):
                raise AssertionError("Invalid Qt byte counters")
            if not any(n > 2**31 for n, _ in counters):
                raise AssertionError("No progress crossed the original overflow boundary")
            capacity = max(8_000_000_000, (path.stat().st_size + 4095) // 4096 * 4096)
            with tempfile.TemporaryDirectory(prefix="autoboot-simulated-disk-") as directory:
                disk_path = Path(directory) / "disk.bin"

                class FileDisk:
                    def __init__(self):
                        self.stream = disk_path.open("w+b", buffering=0)
                        self.stream.truncate(capacity)

                    def seek(self, offset):
                        return self.stream.seek(offset)

                    def write(self, data):
                        return self.stream.write(data)

                    def read(self, size):
                        return self.stream.read(size)

                    def flush(self):
                        self.stream.flush()
                        os.fsync(self.stream.fileno())

                    def reopen_for_verify(self):
                        self.stream.close()
                        self.stream = disk_path.open("rb", buffering=0)

                disk = FileDisk()
                phases = set()
                try:
                    with path.open("rb", buffering=0) as source:
                        validate_hybrid_header(source)
                        digest = copy_and_verify(
                            source,
                            disk,
                            path.stat().st_size,
                            release.sha256,
                            capacity,
                            progress=lambda phase, *_: phases.add(phase),
                        )
                finally:
                    disk.stream.close()
                row = {
                    "filename": release.filename,
                    "image_bytes": path.stat().st_size,
                    "sha256": digest,
                    "qt_worker_hash_matches": hashes == [digest],
                    "progress_over_2GiB": True,
                    "hybrid_header_valid": True,
                    "file_backed_disk_readback_matches": digest == release.sha256,
                    "phases": sorted(phases),
                    "physical_disk_accessed": False,
                }
                print(json.dumps(row), flush=True)
                results.append(row)
    if not results:
        raise ValueError("No curated complete ISOs found")
    report = Path("docs/FILE_BACKED_CHECK.json")
    if args.only and report.exists():
        previous = json.loads(report.read_text())
        names = {r["filename"] for r in results}
        results = [r for r in previous if r["filename"] not in names] + results
    report.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
