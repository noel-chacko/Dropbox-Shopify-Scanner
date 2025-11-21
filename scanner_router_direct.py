#!/usr/bin/env python3
"""
Direct Scanner Router - Uses direct polling instead of watchdog for better network share handling
"""

import os
import time
import json
from pathlib import Path, PurePosixPath
from datetime import datetime
import threading
from typing import Dict, Any, List, Tuple, Optional
import requests
import re
from dotenv import load_dotenv
import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError

# Load environment variables
load_dotenv()

SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/Orders")
NORITSU_ROOT = os.getenv("NORITSU_ROOT")
LAB_NAME = os.getenv("LAB_NAME", "Noritsu")
SETTLE_SECONDS = float(os.getenv("SETTLE_SECONDS", "0.5"))
# How often (seconds) to check the watch directory for new folders
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "2"))
CUSTOMER_LINK_FIELD_NS = os.getenv("CUSTOMER_LINK_FIELD_NS", "custom_fields")
CUSTOMER_LINK_FIELD_KEY = os.getenv("CUSTOMER_LINK_FIELD_KEY", "dropbox")

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and DROPBOX_TOKEN and NORITSU_ROOT, \
    "Missing required .env entries"
# Configure Dropbox client
DBX = dropbox.Dropbox(DROPBOX_TOKEN, timeout=120, max_retries_on_rate_limit=5)
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
                            metafield(namespace:\"{CUSTOMER_LINK_FIELD_NS}\", key:\"{CUSTOMER_LINK_FIELD_KEY}\"){{ value }}
                        }}
                    }}
                }}
            }}
        }}"""
        data = shopify_gql(query, {"q": q})
        return [e["node"] for e in data["orders"]["edges"]]

def set_customer_dropbox_link(customer_gid: str, url: str) -> bool:
    mutation = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id }
        userErrors { field message }
      }
    }"""
    result = shopify_gql(mutation, {"metafields": [{
        "ownerId": customer_gid,
        "namespace": CUSTOMER_LINK_FIELD_NS,
        "key": CUSTOMER_LINK_FIELD_KEY,
        "type": "url",
        "value": url
    }]})

    errors = result.get("metafieldsSet", {}).get("userErrors", [])
    if errors:
        print("⚠️  Failed to update Shopify metafield:")
        for err in errors:
            field = err.get("field", [])
            if isinstance(field, list):
                field = ".".join(field)
            print(f"   Field: {field or 'unknown'} | Message: {err.get('message', 'Unknown error')}")
        return False

    return True


def order_add_tags(order_gid: str, tags: List[str]) -> bool:
    """Add tags to an order using Shopify GraphQL"""
    if not tags:
        return True
    # Use the generic tagsAdd mutation which works for orders and other taggable resources
    mutation = """
    mutation tagsAdd($id: ID!, $tags: [String!]!) {
      tagsAdd(id: $id, tags: $tags) {
        node { id }
        userErrors { field message }
      }
    }"""
    try:
        result = shopify_gql(mutation, {"id": order_gid, "tags": tags})
    except Exception as e:
        # shopify_gql raises when top-level 'errors' exist — show details
        print(f"⚠️  Error adding tags to order (GraphQL error): {e}")
        return False

    # Inspect userErrors from the tagsAdd response
    errors = result.get("tagsAdd", {}).get("userErrors", [])
    if errors:
        print("⚠️  Failed to add tags to order:")
        for err in errors:
            field = err.get("field", [])
            if isinstance(field, list):
                field = ".".join(field)
            print(f"   Field: {field or 'unknown'} | Message: {err.get('message', 'Unknown error')}")
        return False

    print(f"✅ Tags added: {', '.join(tags)}")
    return True


# Dropbox helpers (mirroring create_customer_dropbox)
def ensure_folder(path: str) -> None:
    try:
        DBX.files_create_folder_v2(path, autorename=False)
    except ApiError:
        pass


def ensure_tree(full_path: str) -> None:
    if not full_path or full_path == "/":
        return

    parts = [p for p in PurePosixPath(full_path).parts if p != "/"]
    if not parts:
        return

    try:
        ensure_folder(full_path)
        return
    except Exception:
        pass

    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        ensure_folder(cur)


def make_shared_link(path: str) -> Optional[str]:
    try:
        return DBX.sharing_create_shared_link_with_settings(path).url
    except ApiError:
        try:
            links = DBX.sharing_list_shared_links(path=path).links
            return links[0].url if links else None
        except ApiError as e:
            print(f"⚠️  Could not retrieve shared link for {path}: {e}")
            return None


def ensure_customer_order_folder(order_node: Dict[str, Any]) -> Tuple[str, str]:
    customer = order_node.get("customer") or {}
    email = (customer.get("email") or order_node.get("email") or "unknown").strip().lower()
    customer_gid = customer.get("id")
    # Prefer an existing customer Dropbox root if the customer already has a shared-link saved
    root_path = None
    meta = customer.get("metafield")
    if isinstance(meta, dict):
        existing_link = meta.get("value")
    else:
        existing_link = None

    if existing_link:
        try:
            md = DBX.sharing_get_shared_link_metadata(existing_link)
            path = getattr(md, "path_display", None) or getattr(md, "path_lower", None)
            if path:
                root_path = path
                print(f"ℹ️  Using customer's existing Dropbox root: {root_path}")
            else:
                print(f"⚠️  Shared link exists but no path was available; falling back to default root for {email}")
        except Exception as e:
            print(f"⚠️  Could not resolve customer's shared link metadata: {e}; falling back to default root for {email}")

    # Fallback: default to standard DROPBOX_ROOT/email
    if not root_path:
        root_path = f"{DROPBOX_ROOT}/{email}"
        ensure_tree(root_path)

        link = make_shared_link(root_path)
        if link and customer_gid:
            if set_customer_dropbox_link(customer_gid, link):
                print(f"💾 Shopify metafield updated for {email}")
            else:
                print("⚠️  Metafield update failed; please verify in Shopify.")
        elif customer_gid and not link:
            print(f"⚠️  Could not create shared link for {root_path}; Shopify metafield not updated.")

    order_number = (order_node.get("name") or "").replace('#', '').strip()
    if not order_number:
        order_number = ''.join(ch for ch in (order_node.get("name") or "") if ch.isdigit()) or "order"

    order_path = f"{root_path}/{order_number}"
    try:
        DBX.files_get_metadata(order_path)
        print(f"⚠️  Order folder already exists: {order_path}")
    except ApiError:
        ensure_tree(order_path)
        print(f"📁 Created order folder: {order_path}")

    return root_path, order_path

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

def set_order() -> None:
    """Set the current order interactively"""
    global current_order_data
    
    while True:
        order_num_raw = input("\n🔍 Enter order number (or 'stage'): ").strip()
        if not order_num_raw:
            continue

        # Allow user to quit from this prompt
        if order_num_raw.lower() == "q":
            print("Quitting.")
            os._exit(0)

        if order_num_raw.lower() == "stage":
            with order_lock:
                current_order_data = {"mode": "stage"}
            print("✅ Set to STAGING mode")
            return

        # Parse combined input like '136720s' -> order '136720' and tag 's'
        order_num = order_num_raw
        parsed_tags: List[str] = []
        m = re.match(r"^#?(\d+)(.*)$", order_num_raw)
        if m:
            order_num = m.group(1)
            trailing = (m.group(2) or "").strip()
            if trailing:
                # If trailing starts with comma or space, strip separators
                trailing = trailing.lstrip(' ,')
                # allow multiple comma-separated tags if provided (e.g. 12345s,urgent)
                parsed_tags = [t.strip() for t in re.split(r"[,\s]+", trailing) if t.strip()]
        # Search for order
        # If there are pending tags on the previously-selected order, apply them now
        with order_lock:
            prev = current_order_data
        if prev and isinstance(prev, dict) and prev.get("pending_tags"):
            pending = list(prev.get("pending_tags", []))
            prev_gid = prev.get("order_gid")
            prev_no = prev.get("order_no")
            if prev_gid and pending:
                print(f"\nℹ️ Applying pending tags to previous order {prev_no}: {', '.join(pending)}")
                try:
                    order_add_tags(prev_gid, pending)
                except Exception as e:
                    print(f"⚠️ Error applying pending tags to {prev_no}: {e}")
                finally:
                    with order_lock:
                        # only clear if current_order_data still refers to the same order
                        if isinstance(current_order_data, dict) and current_order_data.get("order_gid") == prev_gid:
                            current_order_data.pop("pending_tags", None)
            else:
                print(f"\nℹ️ No order id or no pending tags to apply for previous selection")

        # Search for order
        results = shopify_search_orders(f"name:{order_num}")
        if not results:
            print("❌ No matches found")
            continue
            
        # Auto-select first match and do a single confirmation (no match list)
        order = results[0]
        tags_confirmed: List[str] = []
        try:
            root_path, order_path = ensure_customer_order_folder(order)
        except Exception as exc:
            print(f"⚠️  Error preparing Dropbox folders: {exc}")
            root_path, order_path = f"{DROPBOX_ROOT}/pending", f"{DROPBOX_ROOT}/pending"

        # Determine tags: use parsed tags from initial input if present, otherwise ask
        tags = parsed_tags
        if not tags:
            tag_input = input("\nWhich tag? (leave blank for none): ").strip()
            tags = [t.strip() for t in tag_input.split(",") if t.strip()] if tag_input else []

        if tags:
            # Single numeric confirmation: show what will be set and require entering '1' to continue
            tag_display = ', '.join(tags)
            confirm = input(f"Order: {order['name']} and Tag: {tag_display}  Enter '1' to continue: ").strip()
            if confirm == '1':
                tags_confirmed = tags
                print(f"ℹ️ Tags for order {order['name']} are saved and will be applied when you enter the next order number.")
            else:
                print("Tagging aborted.")

        with order_lock:
            current_order_data = {
                "order_gid": order["id"],
                "order_no": order["name"],
                "email": order.get("email", "unknown"),
                "order_node": order,
                "dropbox_root_path": root_path,
                "dropbox_order_path": order_path
            }
            if tags_confirmed:
                current_order_data["pending_tags"] = tags_confirmed
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
            order_path = order.get("dropbox_order_path")
            if not order_path:
                _, order_path = ensure_customer_order_folder(order["order_node"])
                with order_lock:
                    if current_order_data:
                        current_order_data["dropbox_order_path"] = order_path
                order["dropbox_order_path"] = order_path
            dest = f"{order_path}/{scan_name}"
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
            # Only scan based on SCAN_INTERVAL
            if time.time() - last_scan < SCAN_INTERVAL:
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