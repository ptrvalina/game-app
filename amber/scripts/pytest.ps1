# Run Amber tests from repo root: npm run test:amber
# Or from amber folder: .\scripts\pytest.ps1

$ErrorActionPreference = "Stop"
$amberRoot = Split-Path -Parent $PSScriptRoot
Set-Location $amberRoot

$venvPy = Join-Path $amberRoot ".venv\Scripts\python.exe"
$venvPip = Join-Path $amberRoot ".venv\Scripts\pip.exe"

if (-not (Test-Path $venvPy)) {
    Write-Error "No .venv in $amberRoot - run: python -m venv .venv; pip install -r requirements.txt"
    exit 1
}

$pyVersion = & $venvPy -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($pyVersion -ne "3.12") {
    Write-Host "WARNING: Amber tests are validated on Python 3.12; current .venv uses $pyVersion" -ForegroundColor Yellow
}

$devReq = Join-Path $amberRoot "requirements-dev.txt"
$ErrorActionPreference = "Continue"
& $venvPy -c "import pytest" 2>$null | Out-Null
$pytestMissing = $LASTEXITCODE -ne 0
$ErrorActionPreference = "Stop"
if ($pytestMissing) {
    Write-Host "Installing requirements-dev.txt (pytest)..."
    & $venvPip install -q -r $devReq
}

& $venvPy -m pytest tests -q --tb=short @args
exit $LASTEXITCODE
