import os
import subprocess
import sys
import unittest
from pathlib import Path


class GuiTests(unittest.TestCase):
    def test_microsoft_browser_form_selects_the_expected_installer(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "tests" / "microsoft_page_fixture.py")],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Microsoft browser form fixture: PASS", result.stdout)

    def test_headless_gui_workflow(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(root))
        result = subprocess.run(
            [sys.executable, str(root / "tests" / "gui_smoke.py")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Qt GUI smoke: PASS", result.stdout)
