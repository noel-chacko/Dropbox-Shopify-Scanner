#!/usr/bin/env python3
"""
Direct Scanner Router - Uses direct polling instead of watchdog for better network share handling
"""

import os
import time
import json
from pathlib import Path
from datetime import datetime
import threading
from typing import Dict, Any, List, Tuple, Optional
import requests
from dotenv import load_dotenv
import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError

# Load environment variables
load_dotenv()

SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/Store/orders")
NORITSU_ROOT = os.getenv("NORITSU_ROOT")
LAB_NAME = os.getenv("LAB_NAME", "Noritsu")
SETTLE_SECONDS = float(os.getenv("SETTLE_SECONDS", "0.5"))

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and DROPBOX_TOKEN and NORITSU_ROOT, \
    "Missing required .env entries"

# Configure Dropbox client
DBX = dropbox.Dropbox(DROPBOX_TOKEN, timeout=120)
SHOPIFY_GRAPHQL = f"https://{SHOPIFY_SHOP}/admin/api/2024-10/graphql.json"
HDR = {"X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN, "Content-Type": "application/json"}

# State management
STATE_FILE = Path(".processed_jobs.json")
def load_state() -> Dict[str, bool]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}

def save_state(state: Dict[str, bool]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))

STATE = load_state()
current_order_data = None
order_lock = threading.Lock()

# Shopify functions
def shopify_gql(query: str, variables=None) -> Dict[str, Any]:
    r = requests.post(SHOPIFY_GRAPHQL, headers=HDR, json={"query": query, "variables": variables or {}}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"Shopify GraphQL error: {data}")
    return data["data"]

def shopify_search_orders(q: str) -> List[Dict[str, Any]]:
    query = f"""
    query($q:String!){{
      orders(first:10, query:$q, sortKey:CREATED_AT, reverse:true){{
        edges{{
          node{{
            id
            name
            email
            displayFulfillmentStatus
            customer{{
              id
              email
              displayName
              metafield(namespace:"custom", key:"dropbox_root_url"){{ value }}
            }}
          }}
        }}
      }}
    }}"""
    data = shopify_gql(query, {"q": q})
    return [e["node"] for e in data["orders"]["edges"]]

def set_customer_dropbox_link(customer_gid: str, url: str) -> None:
    mutation = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) { userErrors { field message } }
    }"""
    shopify_gql(mutation, {"metafields": [{
        "ownerId": customer_gid,
        "namespace": "custom",
        "key": "dropbox_root_url",
        "type": "url",
        "value": url
    }]})

# File operations
def _is_ready(path: Path) -> bool:
    """Check if a directory is ready for processing"""
    try:
        files = list(path.glob("**/*"))
        if not files:
            return False
        
        # Check if files are still being written
        mtime = max(f.stat().st_mtime for f in files if f.is_file())
        return (time.time() - mtime) > SETTLE_SECONDS
    except Exception as e:
        print(f"Error checking {path}: {e}")
        return False

def upload_folder(local_dir: Path, dropbox_path: str) -> int:
    """Upload a folder to Dropbox"""
    count = 0
    try:
        # Ensure target directory exists
        try:
            DBX.files_create_folder_v2(dropbox_path)
        except ApiError:
            pass

        # Upload all files
        for file_path in local_dir.rglob("*"):
            if not file_path.is_file():
                continue
                
            rel_path = file_path.relative_to(local_dir)
            dropbox_file = f"{dropbox_path}/{rel_path}"
            
            try:
                with open(file_path, "rb") as f:
                    DBX.files_upload(f.read(), dropbox_file, mode=WriteMode.overwrite)
                count += 1
            except Exception as e:
                print(f"Error uploading {file_path}: {e}")
                
    except Exception as e:
        print(f"Error processing folder {local_dir}: {e}")
    
    return count

def get_or_create_customer_root(order_node: Dict[str, Any]) -> str:
    """Get or create customer's root Dropbox folder"""
    customer = order_node.get("customer") or {}
    email = (customer.get("email") or order_node.get("email") or "unknown").strip()
    root_path = f"{DROPBOX_ROOT}/{email}"
    
    try:
        DBX.files_create_folder_v2(root_path)
    except ApiError:
        pass
        
    return root_path

def set_order() -> None:
    """Set the current order interactively"""
    global current_order_data
    
    while True:
        order_num = input("\n🔍 Enter order number (or 'stage'): ").strip()
        if not order_num:
            continue
            
        if order_num.lower() == "stage":
            with order_lock:
                current_order_data = {"mode": "stage"}
            print("✅ Set to STAGING mode")
            return
            
        # Search for order
        results = shopify_search_orders(f"name:{order_num}")
        if not results:
            print("❌ No matches found")
            continue
            
        # Show matches
        print("\nMatches found:")
        for i, r in enumerate(results, 1):
            email = r.get("email", "unknown")
            print(f"{i}) #{r['name']} - {email}")
            
        # Pick order
        pick = input("\nPick a number: ").strip()
        if not pick.isdigit():
            continue
            
        idx = int(pick) - 1
        if 0 <= idx < len(results):
            order = results[idx]
            with order_lock:
                current_order_data = {
                    "order_gid": order["id"],
                    "order_no": order["name"],
                    "email": order.get("email", "unknown"),
                    "order_node": order
                }
            print(f"✅ Set to order #{order['name']}")
            return

def process_scan(scan_dir: Path) -> None:
    """Process a single scan directory"""
    global current_order_data
    
    # Get scan details
    scan_name = scan_dir.name
    if STATE.get(scan_name):
        return
        
    # Make sure scan is ready
    if not _is_ready(scan_dir):
        return
        
    # Get current order
    with order_lock:
        order = current_order_data
        
    if not order:
        print(f"\n⚠️ New scan detected: {scan_name}")
        print("No current order set.")
        set_order()
        with order_lock:
            order = current_order_data
    
    # Upload based on order
    try:
        if order.get("mode") == "stage":
            dest = f"{DROPBOX_ROOT}/_staging/{scan_name}"
            print(f"\n📤 Uploading {scan_name} to staging...")
        else:
            customer_root = get_or_create_customer_root(order["order_node"])
            order_number = order['order_no'].replace('#', '')  # Remove any # characters
            dest = f"{customer_root}/{order_number}/{scan_name}"
            print(f"\n📤 Uploading {scan_name} to order #{order['order_no']}...")
        
        uploaded = upload_folder(scan_dir, dest)
        print(f"✅ Uploaded {uploaded} files")
        
        # Mark as processed
        STATE[scan_name] = True
        save_state(STATE)
        
    except Exception as e:
        print(f"❌ Error processing {scan_name}: {e}")

def main():
    print("\n" + "="*60)
    print("📷 DIRECT SCANNER ROUTER")
    print("="*60)
    print(f"Watching: {NORITSU_ROOT}")
    print("="*60)

    # Create initial snapshot of existing folders
    root = Path(NORITSU_ROOT)
    if root.exists():
        existing_folders = {d.name for d in root.iterdir() if d.is_dir()}
        print(f"Found {len(existing_folders)} existing folders - these will be ignored")
    else:
        existing_folders = set()
        print("Warning: Cannot access watch directory for initial snapshot")
    
    # Initial order
    set_order()
    
    # Command handler thread
    def handle_commands():
        while True:
            cmd = input("\nCommands: [Enter]=New Order, q=Quit\n> ").strip().lower()
            if cmd == "q":
                os._exit(0)
            elif cmd == "":
                set_order()
    
    cmd_thread = threading.Thread(target=handle_commands, daemon=True)
    cmd_thread.start()
    
    # Main scanning loop
    root = Path(NORITSU_ROOT)
    last_scan = time.time()
    
    while True:
        try:
            # Only scan every 2 seconds
            if time.time() - last_scan < 2:
                time.sleep(0.1)
                continue
                
            last_scan = time.time()
            
            # Check root exists
            if not root.exists():
                print("⚠️ Cannot access watch directory")
                time.sleep(5)
                continue
                
            # Scan for new directories
            for scan_dir in root.iterdir():
                if not scan_dir.is_dir():
                    continue
                
                # Skip folders that existed when the program started
                if scan_dir.name in existing_folders:
                    continue
                    
                process_scan(scan_dir)
                
        except Exception as e:
            print(f"Error during scan: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()