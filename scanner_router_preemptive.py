#!/usr/bin/env python3
"""
Preemptive scanner router - Enter order first, then subsequent scans auto-upload.

WORKFLOW:
1. Enter order number (once)
2. Scans automatically upload to that order
3. Change order anytime by entering a new number

This eliminates the delay between scan detection and upload.
"""

import os
import time
import json
from pathlib import Path, PurePosixPath
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
import requests

import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError

from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# =================== ENV ===================
load_dotenv()

SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/Store/orders")
NORITSU_ROOT = os.getenv("NORITSU_ROOT")
LAB_NAME = os.getenv("LAB_NAME", "Noritsu")
SETTLE_SECONDS = int(os.getenv("SETTLE_SECONDS", "1"))

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and DROPBOX_TOKEN and NORITSU_ROOT, \
    "Missing required .env entries (SHOPIFY_SHOP, SHOPIFY_ADMIN_TOKEN, DROPBOX_TOKEN, NORITSU_ROOT)."

# Configure Dropbox client
DBX = dropbox.Dropbox(DROPBOX_TOKEN, timeout=120, max_retries_on_rate_limit=5)
SHOPIFY_GRAPHQL = f"https://{SHOPIFY_SHOP}/admin/api/2024-10/graphql.json"
HDR = {"X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN, "Content-Type": "application/json"}

STATE_FILE = Path(".processed_jobs.json")

# =================== STATE ===================
def load_state() -> Dict[str, bool]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: Dict[str, bool]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

STATE = load_state()

# =================== GLOBAL STATE ===================
current_order_lock = threading.Lock()
current_order_data = None  # {"order_gid": "...", "order_no": "...", "email": "...", "order_node": {}}

# =================== SHOPIFY ===================
def shopify_gql(query: str, variables=None) -> Dict[str, Any]:
    r = requests.post(SHOPIFY_GRAPHQL, headers=HDR, json={"query": query, "variables": variables or {}}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "errors" in data or data.get("data") is None:
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

# =================== DROPBOX ===================
SKIP_PATTERNS = (".DS_Store", "Thumbs.db", "desktop.ini",)

def _is_sane_file(fp: Path) -> bool:
    name = fp.name
    if name.startswith(".") or name in SKIP_PATTERNS:
        return False
    try:
        st = fp.stat()
    except FileNotFoundError:
        return False
    if st.st_size == 0:
        return False
    if (time.time() - st.st_mtime) < 0.5:
        return False
    return True

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
    except:
        pass
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        ensure_folder(cur)

@retry(wait=wait_exponential(multiplier=1, min=1, max=30), stop=stop_after_attempt(5))
def upload_file(local_fp: Path, dropbox_dest: str) -> None:
    size = local_fp.stat().st_size
    CHUNK = 32 * 1024 * 1024
    
    with local_fp.open("rb") as f:
        if size <= CHUNK:
            DBX.files_upload(f.read(), dropbox_dest, mode=WriteMode("overwrite"))
        else:
            session = DBX.files_upload_session_start(f.read(CHUNK))
            cursor = dropbox.files.UploadSessionCursor(session.session_id, f.tell())
            commit = dropbox.files.CommitInfo(path=dropbox_dest, mode=WriteMode("overwrite"))
            while f.tell() < size:
                remaining = size - f.tell()
                if remaining <= CHUNK:
                    DBX.files_upload_session_finish(f.read(remaining), cursor, commit)
                else:
                    DBX.files_upload_session_append_v2(f.read(CHUNK), cursor)
                    cursor.offset = f.tell()

def upload_file_wrapper(args):
    fp, dest, rel = args
    try:
        upload_file(fp, dest)
        return True, rel, None
    except ApiError as e:
        return False, rel, f"Dropbox upload failed for {rel}: {e}"
    except Exception as e:
        return False, rel, f"Upload exception for {rel}: {e}"

def upload_folder(local_dir: Path, dropbox_photos_dir: str) -> int:
    files_to_upload = []
    parents_to_ensure = set()
    
    for fp in sorted(local_dir.rglob("*")):
        if not fp.is_file():
            continue
        if not _is_sane_file(fp):
            continue

        rel = fp.relative_to(local_dir).as_posix()
        dest = f"{dropbox_photos_dir}/{rel}"
        parent = str(PurePosixPath(dest).parent)
        if parent and parent != "/":
            parents_to_ensure.add(parent)
        files_to_upload.append((fp, dest, rel))
    
    if not files_to_upload:
        return 0
    
    for parent in sorted(parents_to_ensure):
        ensure_tree(parent)
    
    total = len(files_to_upload)
    count = 0
    with ThreadPoolExecutor(max_workers=32) as executor:
        futures = [executor.submit(upload_file_wrapper, args) for args in files_to_upload]
        for future in as_completed(futures):
            success, rel, error = future.result()
            if success:
                count += 1
            else:
                print(f"[ERROR] {error}")
    
    return count

def get_or_create_customer_root(order_node: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    customer = order_node.get("customer") or {}
    customer_gid = customer.get("id")
    email = (customer.get("email") or order_node.get("email") or "unknown").strip()

    saved_link = (customer.get("metafield") or {}).get("value") if customer else None
    if saved_link:
        path = resolve_link_to_path(saved_link)
        if path:
            return path, saved_link

    root_path = f"{DROPBOX_ROOT}/{email}"
    ensure_tree(root_path)
    link = make_shared_link(root_path)

    if customer_gid and link:
        try:
            set_customer_dropbox_link(customer_gid, link)
        except Exception as e:
            print(f"[WARN] could not save customer link: {e}")

    return root_path, link

def build_dest_paths_from_root(root_path: str, order_no: str, twin_check: str) -> Tuple[str, str]:
    safe_order_no = order_no.replace("#", "").replace("/", "_").replace("\\", "_")
    base = f"{root_path}/{safe_order_no}/{twin_check}"
    return base, base

def make_shared_link(path: str) -> Optional[str]:
    try:
        return DBX.sharing_create_shared_link_with_settings(path).url
    except ApiError:
        links = DBX.sharing_list_shared_links(path=path).links
        return links[0].url if links else None

def resolve_link_to_path(url: str) -> Optional[str]:
    try:
        meta = DBX.sharing_get_shared_link_metadata(url)
        return getattr(meta, "path_lower", None)
    except Exception:
        return None

# =================== NEW: SET ORDER ===================
def set_current_order():
    """Interactive function to set the current order."""
    global current_order_data
    
    # Show current order if set
    with current_order_lock:
        if current_order_data:
            if current_order_data["mode"] == "stage":
                print(f"\n📦 Current: STAGING mode")
            else:
                print(f"\n📦 Current: Order #{current_order_data['order_no']} ({current_order_data['email']})")
    
    while True:
        s = input("\n🔍 Enter order number (or 'stage' for staging): ").strip()
        if not s:
            print("⚠ Please enter an order number or 'stage'")
            continue
            
        if s.lower() == "stage":
            with current_order_lock:
                current_order_data = {"mode": "stage"}
            print("✓ ✓ All subsequent scans will be STAGED")
            return
        
        if s.isdigit():
            q = f"name:{s} OR order_number:{s}"
        else:
            q = s

        print(f"\n🔎 Searching for: {q}...")
        results = shopify_search_orders(q)
        if not results:
            print("❌ No matches. Try again.")
            continue

        print("\n📋 Matches:")
        for i, r in enumerate(results, 1):
            who = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
            print(f"{i:>2})  #{r['name']:<6}  {who:<30}  [{r['displayFulfillmentStatus']}]")

        pick = input("\n👉 Pick a number (or Enter to search again): ").strip()
        if pick.isdigit():
            idx = int(pick)
            if 1 <= idx <= len(results):
                r = results[idx - 1]
                email = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
                with current_order_lock:
                    current_order_data = {
                        "mode": "assign",
                        "order_gid": r["id"],
                        "order_no": str(r["name"]),
                        "email": email,
                        "order_node": r
                    }
                print(f"\n✅ Current order set to: Order #{r['name']}")
                print(f"   Customer: {email}")
                return

# =================== ROUTING ===================
def route_job(date_dir: Path, twin_dir: Path) -> None:
    date_str = date_dir.name
    twin_check = twin_dir.name
    print(f"\n== Detected job: {date_str}/{twin_check} ==")

    with current_order_lock:
        order = current_order_data
    
    if order is None:
        print("[WARN] No order set. Setting order now...")
        set_current_order()
        with current_order_lock:
            order = current_order_data
    
    if order["mode"] == "stage":
        stage_photos_dir = f"{DROPBOX_ROOT}/_staging/{date_str}/{twin_check}/photos"
        uploaded = upload_folder(twin_dir, stage_photos_dir)
        print(f"✓ Staged {uploaded} files to {stage_photos_dir}")
        return
    
    # Upload to order
    customer_root_path, _ = get_or_create_customer_root(order["order_node"])
    photos_dir, _ = build_dest_paths_from_root(customer_root_path, order["order_no"], twin_check)
    
    uploaded = upload_folder(twin_dir, photos_dir)
    print(f"✓ Uploaded {uploaded} files → {photos_dir}")

# =================== WATCHER ===================
class Handler(FileSystemEventHandler):
    def _ready(self, twin_dir: Path) -> bool:
        files = [f for f in twin_dir.glob("**/*") if f.is_file()]
        if not files:
            return False
        newest = max((f.stat().st_mtime for f in files), default=0)
        return (time.time() - newest) > SETTLE_SECONDS

    def _scan_tree(self):
        root = Path(NORITSU_ROOT)
        if not root.exists():
            return

        for date_dir in sorted([d for d in root.iterdir() if d.is_dir()]):
            for twin_dir in sorted([d for d in date_dir.iterdir() if d.is_dir()]):
                key = f"{date_dir.name}/{twin_dir.name}"
                if STATE.get(key):
                    continue
                if not self._ready(twin_dir):
                    continue
                try:
                    STATE[key] = True
                    save_state(STATE)
                    route_job(date_dir, twin_dir)
                except Exception as e:
                    print(f"[ERROR] Routing {key} failed: {e}")

    def on_any_event(self, event):
        self._scan_tree()

# =================== MAIN ===================
def main():
    print(f"Watching {NORITSU_ROOT} … (Ctrl+C to stop)")
    print("\n" + "="*60)
    print("PREEMPTIVE MODE: Set order first, scans auto-upload")
    print("="*60)
    
    # Set initial order
    set_current_order()
    
    handler = Handler()
    observer = Observer()
    observer.schedule(handler, NORITSU_ROOT, recursive=True)
    observer.start()
    
    # Separate thread for order changes
    def order_manager():
        while True:
            try:
                s = input("\n📝 Change order (type number), 'stage' for staging, or Enter: ").strip()
                if s.lower() == "q" or s.lower() == "quit":
                    break
                elif s:
                    set_current_order()
            except EOFError:
                # Handle Ctrl+C gracefully
                break
            except Exception as e:
                print(f"[ERROR] Order manager: {e}")
    
    manager_thread = threading.Thread(target=order_manager, daemon=True)
    manager_thread.start()
    
    try:
        while True:
            time.sleep(2)
            handler._scan_tree()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()

