#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Activate virtual environment if present.
if [ -f ".venv/bin/activate" ]; then
    echo "Activating virtual environment .venv..."
    # shellcheck disable=SC1091
    source ".venv/bin/activate"
    PYTHON=python
else
    if command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    elif command -v python >/dev/null 2>&1; then
        PYTHON=python
    else
        echo "Error: Python 3 is not installed or not in PATH."
        exit 1
    fi
fi

MISSING_REQUIREMENTS=0
for module in PySide6 dropbox dotenv requests tenacity; do
    if ! "$PYTHON" -c "import $module" >/dev/null 2>&1; then
        MISSING_REQUIREMENTS=1
        break
    fi
done

if [ "$MISSING_REQUIREMENTS" -eq 1 ]; then
    echo "Required Python packages are missing. Installing requirements..."
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -r requirements.txt
fi

echo "Launching scanner_router_gui.py..."
"$PYTHON" scanner_router_gui.py
