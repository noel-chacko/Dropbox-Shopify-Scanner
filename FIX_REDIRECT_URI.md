# Fix "Invalid redirect_uri" Error

## The Problem
The redirect URI `http://localhost:8080/callback` must be registered in your Dropbox app settings.

## How to Fix

### Step 1: Go to Dropbox App Settings
1. Go to https://www.dropbox.com/developers/apps
2. Click on your app (or create a new one)
3. Go to the **Settings** tab

### Step 2: Add Redirect URI
1. Scroll down to **OAuth 2** section
2. Find **Redirect URIs** 
3. Click **Add URI** or **+ Add**
4. Enter exactly: `http://localhost:8080/callback`
5. Click **Add** or **Save**

### Step 3: Save Changes
- Make sure to save your changes

### Step 4: Try Again
- Run `python3 get_dropbox_refresh_token.py` again
- It should work now!

## Alternative: Use a Different Redirect URI

If you want to use a different redirect URI:

1. **Update the script**: Change `REDIRECT_URI` in `get_dropbox_refresh_token.py`
2. **Register it**: Add the same URI in your Dropbox app settings
3. **Make sure they match exactly** (including http vs https, port number, etc.)

## Note About Sign-In
- You need to be signed into the **Dropbox account** you want to grant access to
- This doesn't have to be the app owner's account
- Just sign into the Dropbox account that should have access to the files

