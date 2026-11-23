# Builds standalone TikTokStreakKeeper.exe using PyInstaller
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}

# Ensure PyInstaller is installed
& $Py -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..."
    & $Py -m pip install pyinstaller
}

# Generate app_icon_v2.ico if missing
$Ico = Join-Path $Root "logs\app_icon_v2.ico"
if (-not (Test-Path $Ico)) {
    & $Py -c "import gui; from pathlib import Path; gui.ensure_app_ico(Path('logs'))"
}

Write-Host "Building TikTokStreakKeeper.exe (this takes ~30-60 seconds)..."
& $Py -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name "TikTokStreakKeeper" `
    --icon "$Ico" `
    --collect-all customtkinter `
    --collect-all playwright `
    --hidden-import pystray._win32 `
    --hidden-import PIL._tkinter_finder `
    --hidden-import gui `
    --hidden-import menu `
    streak.py

$ExePath = Join-Path $Root "dist\TikTokStreakKeeper.exe"
if (Test-Path $ExePath) {
    Write-Host ""
    Write-Host "Build complete: $ExePath"
} else {
    throw "Build failed: TikTokStreakKeeper.exe was not created."
}
