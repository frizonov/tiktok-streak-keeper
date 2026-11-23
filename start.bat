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

if not exist ".venv\Lib\site-packages\customtkinter" (
    echo Installing GUI dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

start "" ".venv\Scripts\pythonw.exe" streak.py gui
