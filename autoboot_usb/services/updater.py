"""Check GitHub for a newer AutoBoot USB and fast-forward a source checkout."""

from __future__ import annotations

import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from .. import __version__

REPOSITORY = "Wiradjuri/AutoBoot-USB"
BRANCH = "master"
PROJECT_PAGE = f"https://github.com/{REPOSITORY}"
VERSION_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{BRANCH}/autoboot_usb/__init__.py"


class UpdateError(RuntimeError):
    pass


def parse_version(text: str) -> tuple[int, ...]:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)*)\s*", text)
    if not match:
        raise UpdateError(f"Unrecognised version: {text!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(remote: str, local: str = __version__) -> bool:
    a, b = parse_version(remote), parse_version(local)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def latest_version(timeout: float = 15) -> str:
    request = urllib.request.Request(
        VERSION_URL, headers={"User-Agent": f"AutoBootUSB/{__version__}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(64 * 1024).decode("utf-8", "replace")
    except OSError as exc:
        raise UpdateError(f"Could not reach GitHub to check for updates: {exc}") from exc
    match = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", text, re.MULTILINE)
    if not match:
        raise UpdateError("GitHub did not return a recognisable AutoBoot USB version")
    parse_version(match.group(1))
    return match.group(1)


def source_checkout() -> Path | None:
    """The git checkout this copy runs from; None for packaged (frozen) builds."""
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() and (root / "pyproject.toml").is_file() else None


def _git(root: Path, *args: str, timeout: float = 120) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise UpdateError("Git is not installed, so this copy cannot update itself") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"git {args[0]} did not finish in time") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise UpdateError(f"git {args[0]} failed: {detail}")
    return result.stdout.strip()


def apply_update(root: Path) -> None:
    """Fast-forward to GitHub's branch and reinstall. Never discards local work."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != BRANCH:
        raise UpdateError(
            f"This copy is on the '{branch}' branch. Switch to '{BRANCH}' to update automatically"
        )
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise UpdateError("This copy has local changes. Commit or discard them, then update again")
    _git(root, "fetch", "origin", BRANCH, timeout=300)
    _git(root, "merge", "--ff-only", f"origin/{BRANCH}")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-e", str(root)],
            check=True,
            capture_output=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(
            "The code was updated but its dependencies could not be reinstalled. "
            "Run START_WINDOWS.bat after deleting .venv\\.autoboot_installed"
        ) from exc
