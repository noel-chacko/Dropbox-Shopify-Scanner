@echo off
REM Launch the Scanner Order Queue GUI (Windows counterpart to run_order_queue_gui.sh)
REM Pass /quiet to skip interactive pauses (used by the hidden .vbs launcher).

set "QUIET="
if /i "%~1"=="/quiet" set "QUIET=1"

cd /d "%~dp0"

REM Prefer the project virtual environment if present.
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
    goto :found_python
)

REM Otherwise find Python on PATH (python / python3 / py).
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :found_python
)

python3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python3"
    goto :found_python
)

py --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py"
    goto :found_python
)

echo.
echo ========================================
echo Error: Python 3 is not installed or not in PATH
echo ========================================
echo.
echo Install Python 3 from https://www.python.org/downloads/
echo and check "Add Python to PATH" during installation.
echo.
if not defined QUIET pause
exit /b 1

:found_python
echo Found Python: %PYTHON_CMD%

REM Install requirements if any runtime package is missing.
%PYTHON_CMD% -c "import PySide6, dropbox, dotenv, requests, tenacity" >nul 2>&1
if errorlevel 1 (
    echo Required Python packages are missing. Installing requirements...
    %PYTHON_CMD% -m pip install --upgrade pip
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ========================================
        echo Error installing packages
        echo ========================================
        if not defined QUIET pause
        exit /b 1
    )
)

echo Launching scanner_order_queue_gui.py...
%PYTHON_CMD% scanner_order_queue_gui.py

if errorlevel 1 (
    echo.
    echo ========================================
    echo Error occurred. Check the messages above.
    echo ========================================
    if not defined QUIET pause
)
