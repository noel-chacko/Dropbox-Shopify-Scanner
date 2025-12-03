#!/bin/bash
# Build an executable version of the Scanner Router GUI using PyInstaller

echo "Building Scanner Router GUI executable..."
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if PyInstaller is installed
if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller..."
    pip3 install pyinstaller
fi

# Build the executable
echo "Creating executable..."
python3 -m PyInstaller \
    --name="ScannerRouter" \
    --windowed \
    --onefile \
    --add-data "WPPC.jpg:." \
    --hidden-import=PySide6 \
    --hidden-import=dropbox \
    --hidden-import=requests \
    --hidden-import=tenacity \
    scanner_router_gui.py

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Build successful!"
    echo "Executable location: dist/ScannerRouter"
    echo ""
    echo "Note: Make sure your .env file is in the same directory as the executable"
else
    echo ""
    echo "❌ Build failed. Check the error messages above."
    exit 1
fi

