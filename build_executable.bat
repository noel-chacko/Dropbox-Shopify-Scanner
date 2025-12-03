@echo off
REM Build an executable version of the Scanner Router GUI using PyInstaller (Windows)

echo Building Scanner Router GUI executable...
echo.

REM Get the directory where this batch file is located
cd /d "%~dp0"

REM Check if PyInstaller is installed
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Build the executable
echo Creating executable...
python -m PyInstaller ^
    --name=ScannerRouter ^
    --windowed ^
    --onefile ^
    --add-data "WPPC.jpg;." ^
    --hidden-import=PySide6 ^
    --hidden-import=dropbox ^
    --hidden-import=requests ^
    --hidden-import=tenacity ^
    scanner_router_gui.py

if %errorlevel% equ 0 (
    echo.
    echo Build successful!
    echo Executable location: dist\ScannerRouter.exe
    echo.
    echo Note: Make sure your .env file is in the same directory as the executable
) else (
    echo.
    echo Build failed. Check the error messages above.
    pause
    exit /b 1
)

pause

