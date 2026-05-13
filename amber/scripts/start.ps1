# Start Amber (run from repo: use path to this script; cwd becomes amber/)
# Usage: .\scripts\start.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    $OutputEncoding = [Console]::OutputEncoding
} catch {}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    python -m venv .venv
}

$pyVersion = & .\.venv\Scripts\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($pyVersion -ne "3.12") {
    Write-Host "WARNING: Amber is validated on Python 3.12; current .venv uses $pyVersion" -ForegroundColor Yellow
}

if (-not (Test-Path ".\.venv\Scripts\uvicorn.exe")) {
    Write-Host "pip install -r requirements.txt (first time or after dependency change) ..."
    Write-Host "WinError 32? Stop uvicorn (Ctrl+C), close other Python terminals, then: .\scripts\install.ps1" -ForegroundColor DarkYellow
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\pip.exe install -r requirements.txt
    if (-not (Test-Path ".\.venv\Scripts\uvicorn.exe")) {
        Write-Error "uvicorn not installed. Use Python 3.12 if pip still fails (see requirements.txt comment)."
        exit 1
    }
}

Write-Host ""
Write-Host "Server: http://127.0.0.1:8000   Console: http://127.0.0.1:8000/console   (Ctrl+C to stop)"
Write-Host ""
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
