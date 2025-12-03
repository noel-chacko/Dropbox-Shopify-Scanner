@echo off
REM Launch Scanner Router GUI (Windows)

REM Get the directory where this batch file is located
cd /d "%~dp0"

REM Try to find Python (check multiple common commands)
set PYTHON_CMD=
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    goto :found_python
)

python3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python3
    goto :found_python
)

py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    goto :found_python
)

REM Python not found
echo.
echo ========================================
echo Error: Python 3 is not installed or not in PATH
echo ========================================
echo.
echo Please do one of the following:
echo.
echo 1. Install Python 3 from https://www.python.org/downloads/
echo    Make sure to check "Add Python to PATH" during installation
echo.
echo 2. Or if Python is already installed, add it to your PATH:
echo    - Search for "Environment Variables" in Windows
echo    - Add Python installation folder to PATH
echo.
pause
exit /b 1

:found_python
echo Found Python: %PYTHON_CMD%
echo.

REM Run the GUI
%PYTHON_CMD% scanner_router_gui.py

REM Keep window open if there's an error
if errorlevel 1 (
    echo.
    echo ========================================
    echo Error occurred. Check the messages above.
    echo ========================================
    pause
)

