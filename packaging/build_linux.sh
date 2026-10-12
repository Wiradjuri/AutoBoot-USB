#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed --name AutoBootUSB run_autoboot.py
echo "Linux portable PyInstaller directory created at dist/AutoBootUSB."
echo "AppImage integration and distro package metadata are pending."
