@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Python 3.10+ is required. Install Python from python.org and retry.
        pause
        exit /b 1
    )
    py -3 -m venv .venv
    if errorlevel 1 goto :fail
)
if not exist ".venv\.autoboot_installed" (
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto :fail
    echo installed>".venv\.autoboot_installed"
)
".venv\Scripts\python.exe" -m autoboot_usb
if errorlevel 1 goto :fail
exit /b 0
:fail
echo AutoBoot USB could not start. Check the error above.
pause
exit /b 1
