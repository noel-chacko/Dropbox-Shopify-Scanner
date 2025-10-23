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
SETTLE_SECONDS = int(os.getenv("SETTLE_SECONDS", "8"))

AUTO_TAG_S = False  # Disabled due to API version compatibility
CUSTOMER_LINK_FIELD_NS = os.getenv("CUSTOMER_LINK_FIELD_NS", "custom")
CUSTOMER_LINK_FIELD_KEY = os.getenv("CUSTOMER_LINK_FIELD_KEY", "dropbox_root_url")

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and DROPBOX_TOKEN and NORITSU_ROOT, \
    "Missing required .env entries (SHOPIFY_SHOP, SHOPIFY_ADMIN_TOKEN, DROPBOX_TOKEN, NORITSU_ROOT)."

DBX = dropbox.Dropbox(DROPBOX_TOKEN, timeout=120)
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
    # skip 0-byte or still-changing files (conservative: 2s settle)
    if st.st_size == 0:
        return False
    if (time.time() - st.st_mtime) < 2:
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
    parts = [p for p in PurePosixPath(full_path).parts if p != "/"]
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        ensure_folder(cur)

@retry(wait=wait_exponential(multiplier=1, min=1, max=30), stop=stop_after_attempt(5))
def upload_file(local_fp: Path, dropbox_dest: str) -> None:
    size = local_fp.stat().st_size
    CHUNK = 8 * 1024 * 1024
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

def upload_folder(local_dir: Path, dropbox_photos_dir: str) -> int:
    """
    Recursively upload files under local_dir, preserving relative structure
    inside dropbox_photos_dir.
    """
    count = 0
    ensure_tree(dropbox_photos_dir)

    for fp in sorted(local_dir.rglob("*")):
        if not fp.is_file():
            continue
        if not _is_sane_file(fp):
            continue

        rel = fp.relative_to(local_dir).as_posix()
        dest = f"{dropbox_photos_dir}/{rel}"

        # ensure any subfolders exist in Dropbox
        parent = str(PurePosixPath(dest).parent)
        ensure_tree(parent)

        try:
            upload_file(fp, dest)
            count += 1
            print(f"  • uploaded {rel}")
        except ApiError as e:
            print(f"[ERROR] Dropbox upload failed for {rel}: {e}")
        except Exception as e:
            print(f"[ERROR] Upload exception for {rel}: {e}")

    if count == 0:
        print("[WARN] No files matched for upload (check subfolders and temp files).")
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
