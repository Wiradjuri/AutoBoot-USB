#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --onedir --name "AutoBoot USB" run_autoboot.py
hdiutil create -volname "AutoBoot USB" -srcfolder "dist/AutoBoot USB.app" -ov -format UDZO "dist/AutoBootUSB-preview.dmg"
echo "Preview DMG created. Code signing and Apple notarisation are NOT performed."
