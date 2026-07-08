@echo off
:: Genesis Desktop Control Center Launcher
:: Double-click this script to launch the strategy selector GUI window.
cd /d "%~dp0"
start "" ".\.venv\Scripts\pythonw.exe" "gui.py"

