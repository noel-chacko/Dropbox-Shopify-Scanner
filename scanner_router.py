# scanner_router.py
# MVP (no GUI):
# - Watches Noritsu for YYYY-MM-DD/<twin_check> folders
# - When a folder looks "quiet", asks in terminal to search Shopify (email/name/order#)
# - Uploads to Dropbox using Style B:
#     /Store/orders/<email>/<order>/<twin_check>/photos
# - Ensures/creates the customer's root Dropbox link on their Shopify profile
# - Optionally tags the order "s" to trigger your existing Flow

import os
import time
import json
from pathlib import Path, PurePosixPath
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
import threading
import queue

# Global upload queue for background processing
upload_queue = queue.Queue()
upload_executor = None

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
import requests

import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError

# Polling observer works better for network shares (UNC)
from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler

# =================== ENV ===================
load_dotenv()

SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/Store/orders")
NORITSU_ROOT = os.getenv("NORITSU_ROOT")
LAB_NAME = os.getenv("LAB_NAME", "Noritsu")
SETTLE_SECONDS = int(os.getenv("SETTLE_SECONDS", "1"))

AUTO_TAG_S = False  # Disabled due to API version compatibility
CUSTOMER_LINK_FIELD_NS = os.getenv("CUSTOMER_LINK_FIELD_NS", "custom")
CUSTOMER_LINK_FIELD_KEY = os.getenv("CUSTOMER_LINK_FIELD_KEY", "dropbox_root_url")

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and DROPBOX_TOKEN and NORITSU_ROOT, \
    "Missing required .env entries (SHOPIFY_SHOP, SHOPIFY_ADMIN_TOKEN, DROPBOX_TOKEN, NORITSU_ROOT)."

# Configure Dropbox client for better performance
# Using thread-local storage for thread-safe access
_dropbox_storage = threading.local()

def get_dbx():
    """Get a thread-local Dropbox client."""
    if not hasattr(_dropbox_storage, 'dbx'):
        _dropbox_storage.dbx = dropbox.Dropbox(
            DROPBOX_TOKEN, 
            timeout=120,
            max_retries_on_rate_limit=5
        )
    return _dropbox_storage.dbx

# Global DBX for backward compatibility
DBX = get_dbx()
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
              metafield(namespace:"{CUSTOMER_LINK_FIELD_NS}", key:"{CUSTOMER_LINK_FIELD_KEY}"){{ value }}
            }}
          }}
        }}
      }}
    }}"""
    data = shopify_gql(query, {"q": q})
    return [e["node"] for e in data["orders"]["edges"]]

def shopify_update_note(order_gid: str, note: str) -> None:
    mutation = """
    mutation($id:ID!, $note:String){
      orderUpdate(input:{id:$id, note:$note}){ userErrors { field message } }
    }"""
    shopify_gql(mutation, {"id": order_gid, "note": note})

def order_add_tags(order_gid: str, tags: List[str]) -> None:
    mutation = """
    mutation orderTagsAdd($id: ID!, $tags: [String!]!) {
      orderTagsAdd(id: $id, tags: $tags) { userErrors { field message } }
    }"""
    shopify_gql(mutation, {"id": order_gid, "tags": tags})

def set_customer_dropbox_link(customer_gid: str, url: str) -> None:
    mutation = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) { userErrors { field message } }
    }"""
    shopify_gql(mutation, {"metafields": [{
        "ownerId": customer_gid,
        "namespace": CUSTOMER_LINK_FIELD_NS,
        "key": CUSTOMER_LINK_FIELD_KEY,
        "type": "url",  # use "single_line_text_field" if URL type isn't available
        "value": url
    }]})

# =================== DROPBOX ===================

SKIP_PATTERNS = (".DS_Store", "Thumbs.db", "desktop.ini",)

def _is_sane_file(fp: Path) -> bool:
    # ignore hidden files and common OS junk
    name = fp.name
    if name.startswith(".") or name in SKIP_PATTERNS:
        return False
    try:
        st = fp.stat()
    except FileNotFoundError:
        return False
    # skip 0-byte or still-changing files (reduced from 2s to 0.5s for faster processing)
    if st.st_size == 0:
        return False
    if (time.time() - st.st_mtime) < 0.5:
        return False
    return True

def ensure_folder(path: str) -> None:
    """Create a single folder; ignore 'already exists' and races."""
    try:
        DBX.files_create_folder_v2(path, autorename=False)
    except ApiError as e:
        # Swallow 'already exists' or race conditions
        pass

def ensure_tree(full_path: str) -> None:
    """Create every component of the given POSIX path if missing."""
    if not full_path or full_path == "/":
        return
    
    parts = [p for p in PurePosixPath(full_path).parts if p != "/"]
    if not parts:
        return
    
    # Optimize: try to create the entire path at once first
    try:
        ensure_folder(full_path)
        return  # Success, we're done
    except:
        pass
    
    # Fall back to creating each path component
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        ensure_folder(cur)

@retry(wait=wait_exponential(multiplier=1, min=1, max=30), stop=stop_after_attempt(5))
def upload_file(local_fp: Path, dropbox_dest: str) -> None:
    size = local_fp.stat().st_size
    # Increased chunk size for faster uploads (32MB for maximum speed)
    CHUNK = 32 * 1024 * 1024
    # Get thread-local Dropbox client for better performance
    dbx = get_dbx()
    
    with local_fp.open("rb") as f:
        # For files under 32MB, use simple upload (fastest)
        if size <= CHUNK:
            dbx.files_upload(f.read(), dropbox_dest, mode=WriteMode("overwrite"))
        else:
            # For larger files, use chunked upload
            session = dbx.files_upload_session_start(f.read(CHUNK))
            cursor = dropbox.files.UploadSessionCursor(session.session_id, f.tell())
            commit = dropbox.files.CommitInfo(path=dropbox_dest, mode=WriteMode("overwrite"))
            while f.tell() < size:
                remaining = size - f.tell()
                if remaining <= CHUNK:
                    dbx.files_upload_session_finish(f.read(remaining), cursor, commit)
                else:
                    dbx.files_upload_session_append_v2(f.read(CHUNK), cursor)
                    cursor.offset = f.tell()

def upload_file_wrapper(args):
    """Wrapper for parallel upload with error handling."""
    fp, dest, rel = args
    try:
        upload_file(fp, dest)
        return True, rel, None
    except ApiError as e:
        return False, rel, f"Dropbox upload failed for {rel}: {e}"
    except Exception as e:
        return False, rel, f"Upload exception for {rel}: {e}"

def upload_folder(local_dir: Path, dropbox_photos_dir: str) -> int:
    """
    Recursively upload files under local_dir, preserving relative structure
    inside dropbox_photos_dir. Uses parallel uploads for speed.
    """
    # Collect all files to upload
    files_to_upload = []
    parents_to_ensure = set()
    
    for fp in sorted(local_dir.rglob("*")):
        if not fp.is_file():
            continue
        if not _is_sane_file(fp):
            continue

        rel = fp.relative_to(local_dir).as_posix()
        dest = f"{dropbox_photos_dir}/{rel}"
        
        # Track parent directories that need to be created
        parent = str(PurePosixPath(dest).parent)
        if parent and parent != "/":
            parents_to_ensure.add(parent)
        
        files_to_upload.append((fp, dest, rel))
    
    if not files_to_upload:
        print("[WARN] No files matched for upload (check subfolders and temp files).")
        return 0
    
    # Ensure all parent directories exist (batch operation)
    print(f"Ensuring {len(parents_to_ensure)} directories...")
    for parent in sorted(parents_to_ensure):
        ensure_tree(parent)
    
    # Upload files in parallel (up to 32 concurrent uploads for maximum speed)
    total = len(files_to_upload)
    count = 0
    with ThreadPoolExecutor(max_workers=32) as executor:
        # Submit all upload tasks
        futures = [executor.submit(upload_file_wrapper, args) for args in files_to_upload]
        
        # Process completed uploads as they finish
        for future in as_completed(futures):
            success, rel, error = future.result()
            if success:
                count += 1
            else:
                print(f"[ERROR] {error}")
    
    return count

def make_shared_link(path: str) -> Optional[str]:
    try:
        return DBX.sharing_create_shared_link_with_settings(path).url
    except ApiError:
        links = DBX.sharing_list_shared_links(path=path).links
        return links[0].url if links else None

def resolve_link_to_path(url: str) -> Optional[str]:
    try:
        meta = DBX.sharing_get_shared_link_metadata(url)
        # path_lower may be None for non-rooted shared links; handle gracefully
        return getattr(meta, "path_lower", None)
    except Exception:
        return None

# =================== PATHS (Style B) ===================
def build_dest_paths_from_root(root_path: str, order_no: str, twin_check: str) -> Tuple[str, str]:
    # Sanitize order_no for folder path (remove # and other special chars)
    safe_order_no = order_no.replace("#", "").replace("/", "_").replace("\\", "_")
    base = f"{root_path}/{safe_order_no}/{twin_check}"
    return base, base

# =================== CUSTOMER ROOT ===================
def get_or_create_customer_root(order_node: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """
    Returns (customer_root_path, customer_root_link).
    Uses existing link on customer profile if present; otherwise creates /Store/orders/<email> and saves link.
    """
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

# =================== CLI UX ===================
def pick_order_cli() -> Optional[Dict[str, Any]]:
    """
    Type:
      - 'stage' to defer
      - an email (contains '@') to search by email
      - digits to search by order number (name: / order_number:)
      - any text to search by name
    """
    while True:
        s = input("\nSearch Shopify by email/name/order# (or 'stage' to defer): ").strip()
        if not s:
            continue
        if s.lower() == "stage":
            return {"mode": "stage"}

        if "@" in s:
            q = f"email:{s}"
        elif s.isdigit():
            q = f"name:{s} OR order_number:{s}"
        else:
            q = s

        results = shopify_search_orders(q)
        if not results:
            print("No matches. Try again or type 'stage'.")
            continue

        print("\nMatches:")
        for i, r in enumerate(results, 1):
            who = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
            print(f"{i:>2})  #{r['name']:<6}  {who:<30}  [{r['displayFulfillmentStatus']}]")

        pick = input("Pick a number (or Enter to search again): ").strip()
        if pick.isdigit():
            idx = int(pick)
            if 1 <= idx <= len(results):
                r = results[idx - 1]
                email = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
                return {"mode": "assign", "order_gid": r["id"], "order_no": str(r["name"]), "email": email, "order_node": r}

# =================== ROUTING ===================
def route_job(date_dir: Path, twin_dir: Path) -> None:
    date_str = date_dir.name
    twin_check = twin_dir.name
    print(f"\n== Detected job: {date_str}/{twin_check} ==")

    selection = pick_order_cli()
    if selection is None:
        return

    if selection["mode"] == "stage":
        stage_photos_dir = f"{DROPBOX_ROOT}/_staging/{date_str}/{twin_check}/photos"
        uploaded = upload_folder(twin_dir, stage_photos_dir)
        print(f"→ Staged {uploaded} files to {stage_photos_dir}")
        return

    # Ensure customer root exists + link saved on profile
    customer_root_path, _ = get_or_create_customer_root(selection["order_node"])

    # Destination (Style B)
    photos_dir, _link_target = build_dest_paths_from_root(customer_root_path, selection["order_no"], twin_check)

    # Upload
    uploaded = upload_folder(twin_dir, photos_dir)

    # Optional: tag 's' to trigger your Flow
    if AUTO_TAG_S:
        try:
            order_add_tags(selection["order_gid"], ["s"])
        except Exception as e:
            print(f"[WARN] Could not add tag 's': {e}")

    # Optional: order note for audit trail
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        shopify_update_note(selection["order_gid"], f"Scans uploaded ({ts}) via {LAB_NAME}.")
    except Exception as e:
        print(f"[WARN] Could not update note: {e}")

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
            print(f"[WARN] Noritsu root not found: {root}")
            return

        for date_dir in sorted([d for d in root.iterdir() if d.is_dir()]):
            for twin_dir in sorted([d for d in date_dir.iterdir() if d.is_dir()]):
                key = f"{date_dir.name}/{twin_dir.name}"
                if STATE.get(key):
                    continue
                if not self._ready(twin_dir):
                    continue
                try:
                    # Mark as processing immediately to prevent duplicate detection
                    STATE[key] = True
                    save_state(STATE)
                    route_job(date_dir, twin_dir)
                except Exception as e:
                    print(f"[ERROR] Routing {key} failed: {e}")

    def on_any_event(self, event):
        self._scan_tree()

# =================== OPTIONAL: CANARY UPLOAD ===================
def _canary_upload():
    """One-time sanity check that the token/path are valid."""
    try:
        p = Path(".canary.txt")
        p.write_text(f"canary {datetime.now().isoformat()}")
        dest = f"{DROPBOX_ROOT}/_debug/canary.txt"
        ensure_tree(str(PurePosixPath(dest).parent))
        DBX.files_upload(p.read_bytes(), dest, mode=WriteMode("overwrite"))
        print("Dropbox canary upload OK.")
    except Exception as e:
        print(f"[ERROR] Dropbox canary upload failed: {e}")

# =================== MAIN ===================
def main():
    print(f"Watching {NORITSU_ROOT} … (Ctrl+C to stop)")
    # Uncomment once to verify Dropbox credentials and path
    # _canary_upload()

    handler = Handler()
    observer = Observer()
    observer.schedule(handler, NORITSU_ROOT, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(2)
            # Periodic scan to catch folders that became "ready" after settle time
            handler._scan_tree()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
