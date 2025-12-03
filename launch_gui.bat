@echo off
REM Launch Scanner Router GUI (Windows)

REM Get the directory where this batch file is located
cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python 3 and try again
    pause
    exit /b 1
)

REM Run the GUI
python scanner_router_gui.py

REM Keep window open if there's an error
if errorlevel 1 (
    pause
)

