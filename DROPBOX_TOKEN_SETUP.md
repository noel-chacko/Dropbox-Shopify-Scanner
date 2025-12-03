# Dropbox Token Setup Guide

The scanner now has **two separate files** for different token methods:

## Method 1: Simple Token (4-Hour, Manual Refresh) - BACKUP METHOD

**File:** `scanner_router_direct_simple_token.py`

**Use this if the refresh token method has issues.**

### Setup:
1. Get a token from https://www.dropbox.com/developers/apps
2. Add to `.env`:
   ```
   DROPBOX_TOKEN=your_token_here
   ```

3. Run the simple token version:
   ```bash
   python scanner_router_direct_simple_token.py
   ```

### Pros:
- Simple, no OAuth setup needed
- Works immediately
- No complex token refresh logic

### Cons:
- Token expires every 4 hours
- Must manually update token when expired

---

## Method 2: Refresh Token (Automatic Refresh) - RECOMMENDED

**File:** `scanner_router_direct.py` (default, used by GUI)

**This automatically refreshes tokens when they expire.**

### Setup:
1. Get your app credentials from https://www.dropbox.com/developers/apps:
   - `APP_KEY` (also called `client_id`)
   - `APP_SECRET` (also called `client_secret`)

2. Run the helper script:
   ```bash
   python get_dropbox_refresh_token.py
   ```

3. Follow the prompts to authorize and get your refresh token

4. Add to `.env`:
   ```
   DROPBOX_APP_KEY=your_app_key
   DROPBOX_APP_SECRET=your_app_secret
   DROPBOX_REFRESH_TOKEN=your_refresh_token
   # Optional: DROPBOX_TOKEN=initial_token (will be auto-refreshed)
   ```

5. Run the refresh token version:
   ```bash
   python scanner_router_direct.py
   ```

### Pros:
- Automatic token refresh
- No manual updates needed
- Tokens persist across restarts
- **Used by the GUI by default**

### Cons:
- Requires OAuth setup (one-time)
- Slightly more complex initial setup

---

## GUI Usage

The GUI (`scanner_router_gui.py`) uses the **refresh token version** by default.

If you want the GUI to use the simple token version instead, edit `scanner_router_gui.py` line 24:
```python
# Change from:
import scanner_router_direct as router

# To:
import scanner_router_direct_simple_token as router
```

---

## Troubleshooting

### "expired_access_token" errors:
- **Simple mode**: Update `DROPBOX_TOKEN` in `.env` with a new token
- **Refresh mode**: Check that `DROPBOX_REFRESH_TOKEN`, `DROPBOX_APP_KEY`, and `DROPBOX_APP_SECRET` are set correctly

### Token refresh not working:
1. Verify your refresh token is still valid (they can be revoked)
2. Check that app credentials are correct
3. Try switching to simple mode temporarily: `DROPBOX_USE_SIMPLE_TOKEN=true`

### Need to get a new refresh token:
Run `python get_dropbox_refresh_token.py` again to get a new refresh token.

