@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: installing dependencies...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 (
        echo Installation failed.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" streak.py
