# Windows x64 unsigned portable preview. Does not request elevation or touch disks.
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& $Python -m venv .venv-packaging
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$pythonExe = '.\.venv-packaging\Scripts\python.exe'
& $pythonExe -m pip install -r packaging/requirements-windows.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed' }
& $pythonExe -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Application installation failed' }
& $pythonExe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped' }
& $pythonExe packaging/make_icon.py build/icons
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
# Qt-free helper; elevation remains explicitly controlled by ShellExecute runas.
& $pythonExe -m PyInstaller --noconfirm --clean --onefile --windowed --name AutoBootUSBWriter --exclude-module PySide6 run_writer.py
if ($LASTEXITCODE -ne 0) { throw 'Writer helper build failed' }
& $pythonExe -m PyInstaller --noconfirm --clean --onedir --windowed --name AutoBootUSB --icon build/icons/AutoBootUSB.ico --collect-data autoboot_usb --hidden-import PySide6.QtWebEngineWidgets --add-binary 'dist\AutoBootUSBWriter.exe;.' run_autoboot.py
if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
Copy-Item packaging/PORTABLE-README.txt dist/AutoBootUSB/README.txt
if (Test-Path LICENSE) { Copy-Item LICENSE dist/AutoBootUSB/LICENSE.txt }
& $pythonExe -m pip freeze | Set-Content dist/AutoBootUSB/BUILD-DEPENDENCIES.txt
Write-Host 'Unsigned preview: dist\AutoBootUSB\AutoBootUSB.exe'
