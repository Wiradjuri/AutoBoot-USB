"""Update checks against GitHub and fast-forwarding a source checkout, without the network."""

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoboot_usb.services import updater


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class VersionTests(unittest.TestCase):
    def test_newer_versions_compare_numerically(self):
        self.assertTrue(updater.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(updater.is_newer("0.3.1", "0.3"))
        self.assertFalse(updater.is_newer("0.3.0", "0.3"))
        self.assertFalse(updater.is_newer("0.2.9", "0.3.0"))

    def test_malformed_versions_are_rejected(self):
        with self.assertRaises(updater.UpdateError):
            updater.parse_version("1.0-beta")

    def test_latest_version_reads_package_version_from_github(self):
        body = io.BytesIO(b'"""AutoBoot USB."""\n\n__version__ = "9.8.7"\n')
        with patch("urllib.request.urlopen", return_value=body) as urlopen:
            self.assertEqual(updater.latest_version(), "9.8.7")
        self.assertEqual(urlopen.call_args.args[0].full_url, updater.VERSION_URL)

    def test_latest_version_reports_network_failure(self):
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(updater.UpdateError, "offline"):
                updater.latest_version()


@unittest.skipUnless(shutil.which("git"), "git is required")
class ApplyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.remote, self.local = base / "remote", base / "local"
        self.remote.mkdir()
        git(self.remote, "init", "-q", "-b", updater.BRANCH)
        git(self.remote, "config", "user.email", "test@example.com")
        git(self.remote, "config", "user.name", "Test")
        (self.remote / "app.txt").write_text("1\n")
        git(self.remote, "add", ".")
        git(self.remote, "commit", "-q", "-m", "v1")
        git(base, "clone", "-q", str(self.remote), str(self.local))
        (self.remote / "app.txt").write_text("2\n")
        git(self.remote, "commit", "-q", "-am", "v2")

    def tearDown(self):
        self.temp.cleanup()

    def test_fast_forwards_and_reinstalls(self):
        real_run = subprocess.run
        commands = []

        def run(command, *args, **kwargs):
            commands.append(command)
            if "pip" in command:  # Never reinstall the real package from a test.
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        with patch.object(updater.subprocess, "run", side_effect=run):
            updater.apply_update(self.local)
        self.assertEqual((self.local / "app.txt").read_text(), "2\n")
        self.assertTrue(any("pip" in command for command in commands))

    def test_refuses_local_changes(self):
        (self.local / "app.txt").write_text("edited\n")
        with self.assertRaisesRegex(updater.UpdateError, "local changes"):
            updater.apply_update(self.local)
        self.assertEqual((self.local / "app.txt").read_text(), "edited\n")

    def test_refuses_other_branches(self):
        git(self.local, "checkout", "-q", "-b", "feature")
        with self.assertRaisesRegex(updater.UpdateError, "feature"):
            updater.apply_update(self.local)


if __name__ == "__main__":
    unittest.main()
