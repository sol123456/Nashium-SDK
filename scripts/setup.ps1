# One-time setup for a Windows machine that will run Nashium match workers.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
#
# Then, every time you want to work:
#   .\.venv\Scripts\Activate.ps1
#   nashium farm --workers 6
#
# Requires: Python 3.10+ on PATH, and Docker Desktop running.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "python not found on PATH. Install Python 3.10+ from python.org first."
}

Write-Host "==> creating virtual environment (.venv)" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
$py = Join-Path (Get-Location) ".venv\Scripts\python.exe"

Write-Host "==> installing the SDK (editable, with worker extras)" -ForegroundColor Cyan
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -e ".[worker]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host ""
Write-Host "==> checking docker and building the runner image" -ForegroundColor Cyan
# Non-fatal: --check also reports a missing token, which is expected here.
& $py -m nashium farm --build-image --check

Write-Host ""
Write-Host "Next step (once only, stores your token and seed secret):" -ForegroundColor Green
Write-Host '  .\.venv\Scripts\Activate.ps1'
Write-Host '  nashium farm --save-config --token "YOUR_TOKEN" --seed-secret "YOUR_SECRET"'
Write-Host ""
Write-Host "Then:" -ForegroundColor Green
Write-Host "  nashium farm --workers 6"
