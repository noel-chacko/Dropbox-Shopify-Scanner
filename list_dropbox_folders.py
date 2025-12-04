#!/usr/bin/env python3
"""
List folders in Dropbox to verify account and see folder structure
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import dropbox
from dropbox.exceptions import ApiError

# Load environment variables
load_dotenv()

DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET")

# Import token refresh logic from scanner_router_direct
sys.path.insert(0, str(Path(__file__).parent))
try:
    from scanner_router_direct import get_dropbox_client, refresh_dbx_if_needed, DBX
    print("✅ Using Dropbox client from scanner_router_direct")
except ImportError:
    # Fallback: create client directly
    if DROPBOX_REFRESH_TOKEN and DROPBOX_APP_KEY and DROPBOX_APP_SECRET:
        # Try to use refresh token
        import requests
        response = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": DROPBOX_REFRESH_TOKEN,
            },
            auth=(DROPBOX_APP_KEY, DROPBOX_APP_SECRET),
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            access_token = data.get("access_token")
            DBX = dropbox.Dropbox(access_token, timeout=120)
            print("✅ Using refresh token to get access token")
        else:
            print(f"❌ Error refreshing token: {response.text}")
            sys.exit(1)
    elif DROPBOX_TOKEN:
        DBX = dropbox.Dropbox(DROPBOX_TOKEN, timeout=120)
        print("✅ Using DROPBOX_TOKEN from .env")
    else:
        print("❌ No Dropbox credentials found in .env file")
        sys.exit(1)

def list_folders(path="", max_depth=3, current_depth=0, indent=""):
    """Recursively list folders in Dropbox"""
    if current_depth >= max_depth:
        return
    
    try:
        refresh_dbx_if_needed()
        # Use empty string for root, not "/"
        api_path = path if path != "/" else ""
        result = DBX.files_list_folder(api_path)
        
        entries = result.entries
        folders = [e for e in entries if isinstance(e, dropbox.files.FolderMetadata)]
        
        for folder in folders:
            print(f"{indent}📁 {folder.name}")
            # Recursively list subfolders
            list_folders(folder.path_display, max_depth, current_depth + 1, indent + "  ")
        
        # Continue if there are more entries
        while result.has_more:
            result = DBX.files_list_folder_continue(result.cursor)
            entries = result.entries
            folders = [e for e in entries if isinstance(e, dropbox.files.FolderMetadata)]
            for folder in folders:
                print(f"{indent}📁 {folder.name}")
                list_folders(folder.path_display, max_depth, current_depth + 1, indent + "  ")
                
    except ApiError as e:
        print(f"{indent}❌ Error listing {path}: {e}")

def main():
    print("\n" + "="*60)
    print("Dropbox Account Information")
    print("="*60)
    
    try:
        refresh_dbx_if_needed()
        account = DBX.users_get_current_account()
        print(f"\n✅ Connected to Dropbox account:")
        print(f"   Name: {account.name.display_name}")
        print(f"   Email: {account.email}")
        print(f"   Account ID: {account.account_id}")
        
        # Check if it's a team account
        try:
            team_info = DBX.team_info_get_info()
            print(f"\n🏢 Team Account:")
            print(f"   Team Name: {team_info.name}")
            print(f"   Team ID: {team_info.team_id}")
        except Exception:
            print(f"\n⚠️  This appears to be a personal account (not a team account)")
        
    except Exception as e:
        print(f"❌ Error getting account info: {e}")
        return
    
    print("\n" + "="*60)
    print("Folder Structure (showing first 3 levels)")
    print("="*60)
    print("\n📁 / (root)")
    
    list_folders("", max_depth=3)  # Use empty string for root, not "/"
    
    print("\n" + "="*60)
    print("Done!")
    print("="*60)

if __name__ == "__main__":
    main()

