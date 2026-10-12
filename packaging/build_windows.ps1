# Build this unsigned preview on Windows; physical acceptance is still required.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped' }
# Separate narrow helper: no Qt GUI and no manifest that elevates the main app.
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --onefile --windowed --name AutoBootUSBWriter run_writer.py
if ($LASTEXITCODE -ne 0) { throw 'Writer helper build failed' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --onedir --windowed --name AutoBootUSB --add-binary 'dist\AutoBootUSBWriter.exe;.' run_autoboot.py
if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
Write-Host 'Unsigned preview: dist\AutoBootUSB\AutoBootUSB.exe'
Write-Host 'Hardware testing, code signing and a production installer are still required.'
