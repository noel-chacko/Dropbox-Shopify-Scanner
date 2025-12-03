# How to Run the Scanner Router GUI

You have several options to run the GUI without typing commands:

## Option 1: Double-Click App (Easiest for macOS)

### On macOS:
**Just double-click `Scanner Router.app`** - it will launch automatically!

This is an AppleScript app that runs the GUI without opening Terminal.

### Alternative (Linux/macOS Terminal):
1. Open Terminal
2. Navigate to the folder: `cd /path/to/Dropbox+Shopify+Scanner`
3. Run: `./launch_gui.sh`

**Note:** If you get a "permission denied" error, run:
```bash
chmod +x launch_gui.sh
```

### On Windows:
1. Double-click `launch_gui.bat`
2. The GUI will open automatically

---

## Option 2: Create a Standalone Executable (Best for Distribution)

This creates a single file you can run without Python installed.

### On macOS/Linux:
1. Run: `./build_executable.sh`
2. The executable will be in `dist/ScannerRouter`
3. Double-click to run (or drag to Applications folder)

### On Windows:
1. Run: `build_executable.bat`
2. The executable will be in `dist/ScannerRouter.exe`
3. Double-click to run

**Important:** Make sure your `.env` file is in the same folder as the executable!

---

## Option 3: Create a macOS App Bundle (macOS Only)

For a native macOS app experience:

1. Create an app bundle:
   ```bash
   ./build_executable.sh
   ```

2. Create the app structure:
   ```bash
   mkdir -p "Scanner Router.app/Contents/MacOS"
   cp dist/ScannerRouter "Scanner Router.app/Contents/MacOS/"
   cp .env "Scanner Router.app/Contents/" 2>/dev/null || true
   ```

3. Now you can:
   - Double-click "Scanner Router.app" to launch
   - Drag it to Applications folder
   - Add it to Dock

---

## Option 4: Desktop Shortcut (Windows)

1. Right-click `launch_gui.bat`
2. Select "Create Shortcut"
3. Right-click the shortcut → Properties
4. Change icon if desired
5. Drag shortcut to Desktop

---

## Troubleshooting

### "Command not found" or "Python not found"
- Make sure Python 3 is installed
- On macOS: `brew install python3` or download from python.org
- On Windows: Download from python.org and check "Add to PATH" during installation

### "Module not found" errors
- Run: `pip install -r requirements.txt`
- Make sure you're in the project directory

### Executable won't start
- Make sure `.env` file is in the same directory as the executable
- Check that all required environment variables are set in `.env`

---

## Quick Start

**Easiest way:** Just double-click `launch_gui.sh` (macOS) or `launch_gui.bat` (Windows)!

