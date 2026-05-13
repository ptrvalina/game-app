# Install Amber deps into .venv (run from anywhere; cwd = amber/)
# Fixes common WinError 32: close processes that lock site-packages first.

$ErrorActionPreference = "Stop"
$amberRoot = Split-Path -Parent $PSScriptRoot
Set-Location $amberRoot

$venvPython = Join-Path $amberRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "No .venv found. Creating..."
    python -m venv .venv
}

$pyVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($pyVersion -ne "3.12") {
    Write-Host "WARNING: Amber is validated on Python 3.12; current .venv uses $pyVersion" -ForegroundColor Yellow
}

$pattern = [regex]::Escape($amberRoot) + "\\.venv\\"
$locking = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -match $pattern) }

if ($locking) {
    Write-Host ""
    Write-Host "WARNING: These processes lock the venv (stop them, then re-run this script):" -ForegroundColor Yellow
    $locking | ForEach-Object { Write-Host ("  PID {0}: {1}" -f $_.ProcessId, $_.ExecutablePath) }
    Write-Host ""
    Write-Host "Usually: stop 'uvicorn' (Ctrl+C in its terminal), close Cursor/IDE terminals running Python from this project." -ForegroundColor Yellow
    exit 1
}

Write-Host "Upgrading pip..."
& (Join-Path $amberRoot ".venv\Scripts\python.exe") -m pip install --upgrade pip

Write-Host "Installing requirements..."
& (Join-Path $amberRoot ".venv\Scripts\pip.exe") install -r (Join-Path $amberRoot "requirements.txt")

if ($LASTEXITCODE -ne 0) {
    Write-Host "pip failed. If WinError 32 again: reboot or end Python from Task Manager, then run this script again." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Done. Run: .\scripts\start.ps1" -ForegroundColor Green
