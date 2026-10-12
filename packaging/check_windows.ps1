param([Parameter(Mandatory=$true)][string]$PackagePath)
$ErrorActionPreference = 'Stop'
$PackagePath = (Resolve-Path $PackagePath).Path
$exe = Join-Path $PackagePath 'AutoBootUSB.exe'
$internal = Join-Path $PackagePath '_internal'
foreach ($relative in @('AutoBootUSBWriter.exe', 'PySide6\QtWebEngineProcess.exe', 'PySide6\plugins\platforms\qwindows.dll')) {
    if (-not (Test-Path (Join-Path $internal $relative))) { throw "Missing bundled runtime: $relative" }
}
if (-not (Get-ChildItem $internal -Recurse -Filter 'icudtl.dat')) { throw 'Missing WebEngine resources' }
if (-not (Get-ChildItem $internal -Recurse -Filter '*.pak')) { throw 'Missing WebEngine locales/resources' }
$report = Join-Path $PackagePath 'PACKAGING-CHECK.json'
# Relocated app must work without Python, repo, venv or development tools on PATH.
$oldPath = $env:PATH
$oldPythonPath = $env:PYTHONPATH
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONPATH = ''
    $env:QT_QPA_PLATFORM = 'windows'
    $process = Start-Process $exe -ArgumentList @('--packaging-check', "`"$report`"") -WorkingDirectory $env:TEMP -PassThru
    if (-not $process.WaitForExit(120000)) { $process.Kill(); throw 'Packaged launch timed out' }
    if ($process.ExitCode -ne 0) { throw "Packaged launch failed; inspect $report" }
    if ((Get-Content $report -Raw | ConvertFrom-Json).status -ne 'pass') { throw 'Invalid packaging report' }
} finally {
    $env:PATH = $oldPath
    $env:PYTHONPATH = $oldPythonPath
}
