"""Regression checks for multi-gigabyte ISO progress signals.

Qt's Signal(int) is a signed C++ int (32 bits). Real Linux installer ISOs
regularly exceed that range, so our byte counters must use Python objects.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from autoboot_usb.catalogue import BY_KEY
from autoboot_usb.models import format_size

SOURCE = Path(__file__).resolve().parents[1] / "autoboot_usb" / "services" / "download.py"


def _signal_parameter_types(worker_name: str, signal_name: str) -> list[str]:
    """Check declaration even on CI hosts that cannot install Qt."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == worker_name:
            for statement in node.body:
                if not isinstance(statement, ast.Assign):
                    continue
                if not any(
                    isinstance(target, ast.Name) and target.id == signal_name
                    for target in statement.targets
                ):
                    continue
                value = statement.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "Signal"
                ):
                    return [arg.id for arg in value.args if isinstance(arg, ast.Name)]
    raise AssertionError(f"Could not find {worker_name}.{signal_name} Signal declaration")


class LargeIsoProgressTests(unittest.TestCase):
    def test_download_progress_uses_unbounded_byte_counters(self):
        self.assertEqual(
            _signal_parameter_types("ImageDownloader", "progress"), ["object", "object", "float"]
        )

    def test_verification_progress_uses_unbounded_byte_counters(self):
        self.assertEqual(_signal_parameter_types("ImageVerifier", "progress"), ["object", "object"])

    def test_writer_progress_uses_python_byte_counters(self):
        writer_file = SOURCE.with_name("write_worker.py")
        tree = ast.parse(writer_file.read_text(encoding="utf-8"))
        worker = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "UsbWriteWorker"
        )
        declaration = next(
            node.value
            for node in worker.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "progress" for target in node.targets
            )
        )
        self.assertEqual([a.id for a in declaration.args], ["str", "object", "object"])

    def test_size_display_accepts_multi_gigabyte_values(self):
        self.assertEqual(format_size(4_802_478_080), "4.8 GB")
        self.assertEqual(format_size(5_368_709_120), "5.4 GB")

    def test_real_qt_signals_accept_values_over_32bit_range(self):
        try:
            from PySide6.QtCore import QCoreApplication

            from autoboot_usb.services.download import ImageDownloader, ImageVerifier
        except ImportError:
            self.skipTest("PySide6 not installed; signal declarations checked in other tests")

        app = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(app)
        count = 5_368_709_120  # 5 GiB, beyond both signed and unsigned 32-bit limits
        total = 6_442_450_944  # 6 GiB
        verifier = ImageVerifier(Path("unused.iso"))
        received: list[tuple[int, int]] = []
        verifier.progress.connect(lambda read, size: received.append((read, size)))
        verifier.progress.emit(count, total)
        self.assertEqual(received, [(count, total)])

        downloader = ImageDownloader(BY_KEY["kali"].releases[0], Path("."))
        received_download: list[tuple[int, int, float]] = []
        downloader.progress.connect(
            lambda read, size, speed: received_download.append((read, size, speed))
        )
        downloader.progress.emit(count, total, 1_048_576.0)
        self.assertEqual(received_download, [(count, total, 1_048_576.0)])


if __name__ == "__main__":
    unittest.main()
