# Creates the virtual environment, installs dependencies and the Chromium browser.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    if (Get-Command python -ErrorAction SilentlyContinue) { python -m venv .venv }
    else { py -3 -m venv .venv }
    if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Python 3.9+ not found. Install it from python.org" }
}

$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip
& $Py -m pip install -r requirements.txt
& $Py -m playwright install chromium

Write-Host ""
Write-Host "Done. Run start.bat to open the menu."
