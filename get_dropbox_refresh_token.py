#!/usr/bin/env python3
"""
Helper script to get Dropbox refresh token for long-lived access.

Usage:
1. Go to https://www.dropbox.com/developers/apps
2. Create or select your app
3. Get your APP_KEY and APP_SECRET from the app settings
4. Run this script with: python get_dropbox_refresh_token.py
5. Follow the instructions to authorize and get your refresh token
6. Add the refresh token to your .env file as DROPBOX_REFRESH_TOKEN
"""

import os
import requests
from urllib.parse import urlparse, parse_qs
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import sys

# Get app credentials from environment or prompt
APP_KEY = os.getenv("DROPBOX_APP_KEY")
APP_SECRET = os.getenv("DROPBOX_APP_SECRET")

if not APP_KEY:
    APP_KEY = input("Enter your Dropbox APP_KEY: ").strip()

if not APP_SECRET:
    APP_SECRET = input("Enter your Dropbox APP_SECRET: ").strip()

REDIRECT_URI = "http://localhost:8080/callback"

class CallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    
    def do_GET(self):
        if self.path.startswith('/callback'):
            query = urlparse(self.path).query
            params = parse_qs(query)
            
            if 'code' in params:
                CallbackHandler.auth_code = params['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"""
                    <html>
                    <body>
                        <h1>Authorization Successful!</h1>
                        <p>You can close this window and return to the terminal.</p>
                    </body>
                    </html>
                """)
            elif 'error' in params:
                error = params['error'][0]
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(f"""
                    <html>
                    <body>
                        <h1>Authorization Failed</h1>
                        <p>Error: {error}</p>
                    </body>
                    </html>
                """.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress server logs

def get_refresh_token():
    """Get refresh token from Dropbox OAuth"""
    
    # Step 1: Get authorization URL
    auth_url = (
        f"https://www.dropbox.com/oauth2/authorize?"
        f"client_id={APP_KEY}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"response_type=code&"
        f"token_access_type=offline"  # This is key for getting refresh token
    )
    
    print("\n" + "="*60)
    print("Dropbox OAuth Authorization")
    print("="*60)
    print(f"\n1. Opening browser to authorize...")
    print(f"   If browser doesn't open, visit this URL manually:\n")
    print(f"   {auth_url}\n")
    
    # Start local server to receive callback
    server = HTTPServer(('localhost', 8080), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    # Open browser
    webbrowser.open(auth_url)
    
    # Wait for callback
    print("2. Waiting for authorization...")
    print("   (Complete the authorization in your browser)")
    
    timeout = 120  # 2 minutes
    elapsed = 0
    while CallbackHandler.auth_code is None and elapsed < timeout:
        import time
        time.sleep(1)
        elapsed += 1
    
    server.shutdown()
    
    if CallbackHandler.auth_code is None:
        print("\n❌ Authorization timeout or cancelled")
        return None
    
    auth_code = CallbackHandler.auth_code
    print("✅ Authorization code received")
    
    # Step 2: Exchange code for tokens
    print("\n3. Exchanging authorization code for tokens...")
    
    try:
        response = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "code": auth_code,
                "grant_type": "authorization_code",
                "client_id": APP_KEY,
                "client_secret": APP_SECRET,
                "redirect_uri": REDIRECT_URI,
            },
            timeout=30
        )
        response.raise_for_status()
        tokens = response.json()
        
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 14400)  # Default 4 hours
        
        if not refresh_token:
            print("\n⚠️  Warning: No refresh token received!")
            print("   Make sure you included 'token_access_type=offline' in the auth URL")
            return None
        
        print("✅ Tokens received successfully!")
        print("\n" + "="*60)
        print("Add these to your .env file:")
        print("="*60)
        print(f"\nDROPBOX_APP_KEY={APP_KEY}")
        print(f"DROPBOX_APP_SECRET={APP_SECRET}")
        print(f"DROPBOX_REFRESH_TOKEN={refresh_token}")
        print(f"\n# Optional: You can also set DROPBOX_TOKEN={access_token}")
        print(f"# But it will expire in {expires_in // 3600} hours")
        print(f"# The refresh token will be used to get new access tokens automatically")
        print("\n" + "="*60)
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in
        }
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Error exchanging code for tokens: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return None

if __name__ == "__main__":
    try:
        get_refresh_token()
    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user")
        sys.exit(1)

