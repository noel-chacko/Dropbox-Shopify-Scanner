# Shopify + Dropbox Scanner — macOS installation & run guide

This repository routes Noritsu scanner output into customer Dropbox folders and updates Shopify orders with links. The steps below are tailored for macOS (Intel/M1/M2). Follow them to set up the project, get Dropbox tokens, run the app locally, and optionally build an executable.
### Quick contract
- Inputs: scanner folder tree (Noritsu), Shopify API credentials, Dropbox app credentials
- Outputs: uploaded photos to Dropbox, Shopify customer metafield updated with Dropbox link
- Success criteria: a scanned folder uploaded and linked to the correct Shopify order
- Error modes: missing env vars, token permissions, wrong scanner path

## 1) Prerequisites (macOS)
- macOS 10.14+ recommended (Intel or Apple Silicon)
- Homebrew (optional but recommended)
- Python 3.10 or 3.11 (project tested on 3.10/3.11)
- Git (to clone or update repo)

Install Homebrew (if you don't have it):
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install Python (if needed):
```bash
# Intel mac
brew install python@3.11
# Apple Silicon: brew will choose the right binary
```

Confirm python points to the correct version:
```bash
python3 --version
```

## 2) Clone repository (if not already)

```bash
git clone <your-repo-url>
cd Dropbox+Shopify+Scanner
```

## 3) Create and activate a virtual environment

Use a venv in the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Notes:
- `requirements.txt` contains the Python dependencies. If you add packages, pin them here.

## 4) Environment variables (.env)

Copy the template and edit it:
```bash
cp env_template.txt .env
open .env
```

Required important variables (common ones in this project):
- SHOPIFY_STORE: your-shop-name (used to build admin URLs)
- SHOPIFY_ACCESS_TOKEN: admin API token for your custom app
- DROPBOX_APP_KEY / DROPBOX_APP_SECRET: for the web OAuth flow (if used)
- DROPBOX_REFRESH_TOKEN or DROPBOX_ACCESS_TOKEN: may be required depending on workflow
- NORITSU_ROOT: path the app watches for completed raw scans (e.g. `/Volumes/Noritsu/Store/orders`)

See `env_template.txt` for the full list. If you use the GUI or the simple token script, you may be able to provide a short-lived token directly.

## 7) Run the app (CLI)

- `scanner_router_gui.py` — launches a GUI

Example (CLI):

python3 scanner_router_gui.py

## 10) Troubleshooting checklist

- "Missing .env entries": ensure `.env` contains all required keys from `env_template.txt`.
- "Noritsu root not found": ensure `NORITSU_ROOT` is correct and accessible. If the scanner writes to an external drive, mount it before starting the app.
- Dropbox permission errors: re-check app scopes in Dropbox Developer console and ensure the token corresponds to the app/account.
- Shopify GraphQL errors: check `SHOPIFY_ACCESS_TOKEN` scope and store name. Use Admin > Apps > Your app > API credentials to regenerate if needed.

Logs: the main scripts print errors to the console. Run them from Terminal so you can copy error output for debugging.

## 12) Where to look next in this repo

- `env_template.txt` — the env variables you must fill
- `DROPBOX_TOKEN_SETUP.md` and `get_dropbox_refresh_token.py` — token guidance
- `scanner_router*.py` — main entry points
- `reassign_staged.py` — staged reassignments
- `requirements.txt` — Python deps