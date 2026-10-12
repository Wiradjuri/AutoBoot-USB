@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Python 3.10+ and the Python Launcher are required.
        pause
        exit /b 1
    )
    py -3 -m venv .venv
    if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :fail
echo.
echo Running AutoBoot USB tests. These do not write physical disks.
".venv\Scripts\python.exe" -m unittest discover -s tests -v
set "result=%ERRORLEVEL%"
echo.
if "%result%"=="0" (
    echo All tests passed or were explicitly skipped.
) else (
    echo One or more tests failed. Save the output above for troubleshooting.
)
pause
exit /b %result%
:fail
echo Test environment could not be prepared.
pause
exit /b 1
