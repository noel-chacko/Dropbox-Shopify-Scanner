#!/usr/bin/env python3
"""
Batch scanner router - Queue multiple scans, then upload all at once.

WORKFLOW:
1. Set order number once
2. Scans queue automatically
3. Upload all queued scans in one batch
4. Much faster for high-volume processing

Perfect for: 1.5 min per roll scenarios
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

# =================== QUEUE SYSTEM ===================
scan_queue = []  # List of (date_str, twin_check, twin_dir, order_gid, order_no, email, order_node)
queue_lock = threading.Lock()
default_order_data = None  # Default order for auto-queuing

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
        return False, rel, f"Dropbox upload failed: {e}"
    except Exception as e:
        return False, rel, f"Upload exception: {e}"

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
    with ThreadPoolExecutor(max_workers=64) as executor:  # More workers for batch
        futures = [executor.submit(upload_file_wrapper, args) for args in files_to_upload]
        for future in as_completed(futures):
            success, rel, error = future.result()
            if success:
                count += 1
    
    return count

def get_or_create_customer_root(order_node: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    customer = order_node.get("customer") or {}
    customer_gid = customer.get("id")
    email = (customer.get("email") or order_node.get("email") or "unknown").strip()
    
    saved_link = (customer.get("metafield") or {}).get("value") if customer else None
    if saved_link:
        return saved_link, saved_link
    
    root_path = f"{DROPBOX_ROOT}/{email}"
    ensure_tree(root_path)
    link = make_shared_link(root_path)
    
    if customer_gid and link:
        try:
            set_customer_dropbox_link(customer_gid, link)
        except:
            pass
    
    return root_path, link

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
    except:
        return None

# =================== SET ORDER ===================
def set_current_order():
    global default_order_data
    
    with queue_lock:
        if default_order_data:
            if default_order_data["mode"] == "stage":
                print(f"\n📦 Current: STAGING mode")
            else:
                print(f"\n📦 Current: Order #{default_order_data['order_no']} ({default_order_data['email']})")
    
    while True:
        s = input("\n🔍 Enter order number (or 'stage'): ").strip()
        if not s:
            print("⚠ Please enter an order number or 'stage'")
            continue
            
        if s.lower() == "stage":
            default_order_data = {"mode": "stage"}
            print("✓ All scans will be STAGED")
            return
        
        if s.isdigit():
            q = f"name:{s} OR order_number:{s}"
        else:
            q = s

        results = shopify_search_orders(q)
        if not results:
            print("❌ No matches.")
            continue

        print("\n📋 Matches:")
        for i, r in enumerate(results, 1):
            who = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
            print(f"{i:>2})  #{r['name']:<6}  {who:<30}  [{r['displayFulfillmentStatus']}]")

        pick = input("\n👉 Pick a number: ").strip()
        if pick.isdigit():
            idx = int(pick)
            if 1 <= idx <= len(results):
                r = results[idx - 1]
                email = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
                default_order_data = {
                    "mode": "assign",
                    "order_gid": r["id"],
                    "order_no": str(r["name"]),
                    "email": email,
                    "order_node": r
                }
                print(f"\n✅ Order set: #{r['name']} ({email})")
                return

# =================== ASSIGN ORDERS ===================
def assign_order_to_queued_scan():
    """Assign an order to a queued scan that doesn't have one."""
    with queue_lock:
        unassigned = [(i, item) for i, item in enumerate(scan_queue) if item[3] is None]
        if not unassigned:
            print("✓ All scans have orders assigned")
            return
        
        idx, (date_str, twin_check, twin_dir, _, _, _, _) = unassigned[0]
        print(f"\n📝 Assigning order to: {date_str}/{twin_check}")
    
    # Search for order
    while True:
        s = input("🔍 Enter order number (or 'stage' for staging): ").strip()
        if not s:
            continue
            
        if s.lower() == "stage":
            with queue_lock:
                scan_queue[idx] = (date_str, twin_check, twin_dir, None, "staged", None, {"mode": "stage"})
            print("✓ Will stage this scan")
            return
        
        if s.isdigit():
            q = f"name:{s} OR order_number:{s}"
        else:
            q = s

        results = shopify_search_orders(q)
        if not results:
            print("❌ No matches.")
            continue

        print("\n📋 Matches:")
        for i, r in enumerate(results, 1):
            who = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
            print(f"{i:>2})  #{r['name']:<6}  {who:<30}  [{r['displayFulfillmentStatus']}]")

        pick = input("👉 Pick a number: ").strip()
        if pick.isdigit():
            idx_pick = int(pick)
            if 1 <= idx_pick <= len(results):
                r = results[idx_pick - 1]
                email = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
                with queue_lock:
                    scan_queue[idx] = (date_str, twin_check, twin_dir, r["id"], str(r["name"]), email, r)
                print(f"✓ Assigned: Order #{r['name']} ({email})")
                return

def reassign_order():
    """Change the order for a specific queued scan."""
    with queue_lock:
        if not scan_queue:
            print("📋 Queue is empty")
            return
        
        print("\n📋 Queued scans:")
        for i, (date, twin, _, order_gid, order_no, email, _) in enumerate(scan_queue):
            if order_gid:
                print(f"{i+1:>2}) {date}/{twin} → Order #{order_no} ({email})")
            else:
                print(f"{i+1:>2}) {date}/{twin} → [NEEDS ORDER]")
        
        pick = input("\n👉 Pick scan number to reassign: ").strip()
        if not pick.isdigit():
            print("❌ Invalid number")
            return
        
        idx = int(pick) - 1
        if idx < 0 or idx >= len(scan_queue):
            print("❌ Invalid number")
            return
    
    # Same logic as assign_order_to_queued_scan
    date_str, twin_check, twin_dir, _, _, _, _ = scan_queue[idx]
    while True:
        s = input("🔍 Enter order number (or 'stage'): ").strip()
        if not s:
            continue
            
        if s.lower() == "stage":
            with queue_lock:
                scan_queue[idx] = (date_str, twin_check, twin_dir, None, "staged", None, {"mode": "stage"})
            print("✓ Will stage this scan")
            return
        
        if s.isdigit():
            q = f"name:{s} OR order_number:{s}"
        else:
            q = s

        results = shopify_search_orders(q)
        if not results:
            print("❌ No matches.")
            continue

        print("\n📋 Matches:")
        for i, r in enumerate(results, 1):
            who = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
            print(f"{i:>2})  #{r['name']:<6}  {who:<30}  [{r['displayFulfillmentStatus']}]")

        pick = input("👉 Pick a number: ").strip()
        if pick.isdigit():
            idx_pick = int(pick)
            if 1 <= idx_pick <= len(results):
                r = results[idx_pick - 1]
                email = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
                with queue_lock:
                    scan_queue[idx] = (date_str, twin_check, twin_dir, r["id"], str(r["name"]), email, r)
                print(f"✓ Reassigned: Order #{r['name']} ({email})")
                return

# =================== BATCH UPLOAD ===================
def upload_queued_scans():
    with queue_lock:
        if not scan_queue:
            print("📋 Queue is empty")
            return
        
        # Check for unassigned scans
        unassigned = [i for i, job in enumerate(scan_queue) if job[3] is None]
        if unassigned:
            print(f"⚠ {len(unassigned)} scan(s) need order assignment")
            return
        
        jobs = scan_queue.copy()
        scan_queue.clear()
    
    print(f"\n{'='*60}")
    print(f"🚀 UPLOADING {len(jobs)} SCANS")
    print(f"{'='*60}\n")
    
    for date_str, twin_check, twin_dir, order_gid, order_no, email, order_node in jobs:
        print(f"\n📤 Processing: {date_str}/{twin_check} → Order #{order_no} ({email})")
        
        if order_no == "staged":
            dest = f"{DROPBOX_ROOT}/_staging/{date_str}/{twin_check}/photos"
        else:
            customer_root, _ = get_or_create_customer_root(order_node)
            safe_order = order_no.replace("#", "").replace("/", "_")
            dest = f"{customer_root}/{safe_order}/{twin_check}"
        
        uploaded = upload_folder(twin_dir, dest)
        print(f"✓ Uploaded {uploaded} files")
    
    print(f"\n{'='*60}")
    print(f"✅ ALL {len(jobs)} SCANS UPLOADED")
    print(f"{'='*60}")

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
                
                STATE[key] = True
                save_state(STATE)
                
                with queue_lock:
                    # Use current default order or mark as needing assignment
                    if default_order_data:
                        scan_queue.append((
                            date_dir.name, twin_dir.name, twin_dir,
                            default_order_data.get("order_gid"),
                            default_order_data.get("order_no"),
                            default_order_data.get("email"),
                            default_order_data.get("order_node")
                        ))
                        print(f"📥 Queued: {date_dir.name}/{twin_dir.name} → Order {default_order_data.get('order_no', '?')} (Queue: {len(scan_queue)})")
                    else:
                        scan_queue.append((
                            date_dir.name, twin_dir.name, twin_dir,
                            None, None, None, None
                        ))
                        print(f"📥 Queued: {date_dir.name}/{twin_dir.name} [NEEDS ORDER ASSIGNMENT] (Queue: {len(scan_queue)})")

    def on_any_event(self, event):
        self._scan_tree()

# =================== MAIN ===================
def main():
    print(f"\n{'='*60}")
    print("🚀 BATCH SCANNER ROUTER - High Volume Mode")
    print(f"{'='*60}")
    print(f"Watching: {NORITSU_ROOT}")
    print("="*60)
    
    set_current_order()
    
    handler = Handler()
    observer = Observer()
    observer.schedule(handler, NORITSU_ROOT, recursive=True)
    observer.start()
    
    def command_handler():
        while True:
            try:
                cmd = input("\n💡 Commands:\n  [Enter] = Upload queued scans\n  'assign' = Assign orders to scans\n  'reassign' = Reassign scan orders\n  'order' = Set default order\n  'q' = Quit\n\n❯ ").strip().lower()
                
                if cmd == "q" or cmd == "quit":
                    print("\n👋 Goodbye!")
                    os._exit(0)
                elif cmd == "order":
                    set_current_order()
                elif cmd == "assign":
                    assign_order_to_queued_scan()
                elif cmd == "reassign":
                    reassign_order()
                elif cmd == "" or cmd == "upload":
                    upload_queued_scans()
                else:
                    print("⚠ Unknown command")
            except EOFError:
                break
            except Exception as e:
                print(f"[ERROR] {e}")
    
    cmd_thread = threading.Thread(target=command_handler, daemon=True)
    cmd_thread.start()
    
    try:
        while True:
            time.sleep(2)
            handler._scan_tree()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()

