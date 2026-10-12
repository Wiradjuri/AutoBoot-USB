"""Real isolated helper processes. No elevation or physical disk access."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from autoboot_usb.catalogue import BY_KEY
from autoboot_usb.models import UsbDevice
from autoboot_usb.services.writer_session import create_session, read_message

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.session = create_session()
        self.addCleanup(shutil.rmtree, self.session)
        self.request = self.session / "request.json"
        self.data = {
            "image": str(self.session / "not-opened.iso"),
            "image_size": 1024,
            "sha256": BY_KEY["kali"].releases[0].sha256,
            "target": asdict(
                UsbDevice(
                    "PhysicalDrive2",
                    "Test USB",
                    16_000_000_000,
                    "usb",
                    True,
                    False,
                    serial="TEST-2",
                )
            ),
            "confirmed": True,
        }
        self.digest = self.save_request()

    def save_request(self):
        raw = json.dumps(self.data).encode("utf-8")
        self.request.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def launch(self, helper=None, digest=None):
        # Uses the actual installed interpreter with site hooks disabled, just
        # like the elevated source command. The ISO deliberately does not exist.
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(helper or ROOT / "run_writer.py"),
                "--check",
                str(self.request),
                digest or self.digest,
            ],
            cwd=self.session,
            env=dict(os.environ, PYTHONPATH="invalid-untrusted-import-root"),
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_helper_starts_in_isolated_process_without_qt_or_opening_image(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            read_message(self.session, "ready.jsonl"),
            {"state": "ready"},
        )
        self.assertEqual(read_message(self.session, "status.jsonl"), {})
        self.assertFalse(Path(self.data["image"]).exists())

    def test_actual_worker_command_passes_startup_check(self):
        # On Windows this executes the real pythonw.exe selected by the GUI,
        # not only a console Python or a mocked subprocess launcher.
        from autoboot_usb.services.write_worker import check_helper_startup, helper_command

        executable, args = helper_command(self.session, self.digest)
        check_helper_startup(executable, args, self.session)
        self.assertEqual(
            read_message(self.session, "ready.jsonl"),
            {"state": "ready"},
        )
        self.assertEqual(read_message(self.session, "status.jsonl"), {})
        self.assertFalse(Path(self.data["image"]).exists())

    def test_missing_application_import_is_reported_before_main(self):
        broken = self.session / "broken-app"
        broken.mkdir()
        script = broken / "run_writer.py"
        shutil.copy2(ROOT / "run_writer.py", script)
        result = self.launch(helper=script)
        self.assertEqual(result.returncode, 1)
        status = read_message(self.session, "status.jsonl")
        self.assertEqual(status["state"], "failed")
        self.assertIn("ModuleNotFoundError", status["message"])
        self.assertIn("Traceback", status["traceback"])
        self.assertLess((self.session / "status.jsonl").stat().st_size, 16384)

    def test_invalid_schema_failure_has_specific_diagnostic(self):
        self.data["confirmed"] = False
        self.digest = self.save_request()
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        status = read_message(self.session, "status.jsonl")
        self.assertIn("acknowledgement", status["message"])
        self.assertEqual(read_message(self.session, "ready.jsonl"), {})

    def test_tampered_request_cannot_write_diagnostic_or_readiness(self):
        result = self.launch(digest="0" * 64)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(read_message(self.session, "status.jsonl"), {})
        self.assertEqual(read_message(self.session, "ready.jsonl"), {})

    def test_session_symlink_is_rejected_without_output(self):
        link = self.session / "linked-request.json"
        try:
            link.symlink_to(self.request)
        except OSError:
            self.skipTest("OS does not permit creating symlinks for this account")
        # Keep the required filename by linking the entire session directory.
        with tempfile.TemporaryDirectory() as parent:
            session_link = Path(parent) / "autoboot-write-linked"
            try:
                session_link.symlink_to(self.session, target_is_directory=True)
            except OSError:
                self.skipTest("OS does not permit directory symlinks for this account")
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(ROOT / "run_writer.py"),
                    "--check",
                    str(session_link / "request.json"),
                    self.digest,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(read_message(self.session, "ready.jsonl"), {})
        self.assertEqual(read_message(self.session, "status.jsonl"), {})
