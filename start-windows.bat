@echo off
cd /d "%~dp0"

where pyw >nul 2>nul
if %errorlevel% equ 0 (
  start "" pyw -3 webos_root_wizard.py
  exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel% equ 0 (
  start "" pythonw webos_root_wizard.py
  exit /b 0
)

echo Python 3 was not found. Install it from https://www.python.org/downloads/
pause
