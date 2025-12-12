#!/usr/bin/env python3
"""
Direct Scanner Router - Uses direct polling instead of watchdog for better network share handling

VERSION: With Refresh Token Support (Auto-refreshes tokens)
For simple 4-hour token version, use scanner_router_direct_simple_token.py
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
import traceback
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError, RateLimitError, AuthError

# Error log file for Dropbox errors
DROPBOX_ERROR_LOG_FILE = Path(__file__).parent / "dropbox_errors.log"

def log_dropbox_error(operation: str, error: Exception, context: str = ""):
    """Log Dropbox errors to file in real-time"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_type = type(error).__name__
        error_msg = str(error)
        trace = traceback.format_exc()
        
        with open(DROPBOX_ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] Dropbox Error: {operation}\n")
            if context:
                f.write(f"Context: {context}\n")
            f.write(f"Error Type: {error_type}\n")
            f.write(f"Error Message: {error_msg}\n")
            f.write(f"{'='*80}\n")
            f.write(f"Traceback:\n{trace}\n")
            f.write(f"{'='*80}\n\n")
            f.flush()  # Flush immediately for real-time logging
    except Exception as e:
        # If we can't write to log file, at least print it
        print(f"Failed to write to Dropbox error log: {e}")
        print(f"Original Dropbox error: {error}")

from dropbox.common import PathRootError

# Load environment variables
load_dotenv()

SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET")
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/Orders")
NORITSU_ROOT_BASE = os.getenv("NORITSU_ROOT", "")
# Auto-set to today's date on startup
_today_str = datetime.now().strftime("%Y%m%d")
if NORITSU_ROOT_BASE:
    # Handle path separators correctly (preserve UNC format for Windows)
    if NORITSU_ROOT_BASE.startswith("\\\\"):
        # Preserve UNC path format for Windows (\\server\share)
        NORITSU_ROOT = f"{NORITSU_ROOT_BASE}\\{_today_str}"
    else:
        # Use os.path.join for regular paths
        NORITSU_ROOT = os.path.join(NORITSU_ROOT_BASE, _today_str)
else:
    NORITSU_ROOT = NORITSU_ROOT_BASE  # Fallback if no base path
LAB_NAME = os.getenv("LAB_NAME", "Noritsu")

# Lock for changing NORITSU_ROOT
_noritsu_root_lock = threading.Lock()

def set_noritsu_root(new_path: str) -> bool:
    """Set the NORITSU_ROOT path dynamically"""
    global NORITSU_ROOT
    try:
        test_path = Path(new_path)
        if not test_path.exists():
            return False
        with _noritsu_root_lock:
            NORITSU_ROOT = new_path
        return True
    except Exception:
        return False

def get_noritsu_root() -> str:
    """Get current NORITSU_ROOT path"""
    with _noritsu_root_lock:
        return NORITSU_ROOT

def get_noritsu_base() -> str:
    """Get base NORITSU_ROOT path (without date)"""
    return NORITSU_ROOT_BASE
SETTLE_SECONDS = float(os.getenv("SETTLE_SECONDS", "0.5"))
# How often (seconds) to check the watch directory for new folders
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "2"))
CUSTOMER_LINK_FIELD_NS = os.getenv("CUSTOMER_LINK_FIELD_NS", "custom_fields")
CUSTOMER_LINK_FIELD_KEY = os.getenv("CUSTOMER_LINK_FIELD_KEY", "dropbox")

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and NORITSU_ROOT_BASE, \
    "Missing required .env entries (SHOPIFY_SHOP, SHOPIFY_ADMIN_TOKEN, NORITSU_ROOT)"

# Refresh token mode: need either token or refresh token setup
assert DROPBOX_TOKEN or (DROPBOX_REFRESH_TOKEN and DROPBOX_APP_KEY and DROPBOX_APP_SECRET), \
    "Missing Dropbox credentials (need DROPBOX_TOKEN or DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY + DROPBOX_APP_SECRET)"

# Token storage file
TOKEN_FILE = Path(".dropbox_tokens.json")

def load_tokens() -> Dict[str, Any]:
    """Load tokens from file"""
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}

def save_tokens(tokens: Dict[str, Any]) -> None:
    """Save tokens to file"""
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")

def refresh_access_token() -> Optional[str]:
    """Refresh the Dropbox access token using refresh token"""
    if not DROPBOX_REFRESH_TOKEN or not DROPBOX_APP_KEY or not DROPBOX_APP_SECRET:
        return None
    
    try:
        response = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": DROPBOX_REFRESH_TOKEN,
            },
            auth=(DROPBOX_APP_KEY, DROPBOX_APP_SECRET),
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")  # May not always be returned
        
        # Save tokens
        tokens = {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token or DROPBOX_REFRESH_TOKEN,  # Keep old if not provided
            "expires_at": time.time() + data.get("expires_in", 14400)  # Default 4 hours
        }
        save_tokens(tokens)
        
        return new_access_token
    except Exception as e:
        print(f"⚠️  Error refreshing Dropbox token: {e}")
        return None

def get_dropbox_client():
    """Get or create Dropbox client with automatic token refresh"""
    # Try to load from file first (preferred)
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    expires_at = tokens.get("expires_at", 0)
    
    # Check if saved token is still valid (refresh 1 hour before expiry)
    if access_token and time.time() < (expires_at - 3600):
        try:
            test_client = dropbox.Dropbox(access_token, timeout=10)
            test_client.users_get_current_account()
            return dropbox.Dropbox(access_token, timeout=120, max_retries_on_rate_limit=5)
        except (ApiError, AuthError, Exception):
            # Token expired or invalid, try to refresh
            pass
    
    # Try to refresh token if we have refresh token
    if DROPBOX_REFRESH_TOKEN:
        new_token = refresh_access_token()
        if new_token:
            return dropbox.Dropbox(new_token, timeout=120, max_retries_on_rate_limit=5)
    
    # Fallback to environment token (may be expired, but will be refreshed on first use)
    if DROPBOX_TOKEN:
        try:
            test_client = dropbox.Dropbox(DROPBOX_TOKEN, timeout=10)
            test_client.users_get_current_account()
            return dropbox.Dropbox(DROPBOX_TOKEN, timeout=120, max_retries_on_rate_limit=5)
        except (ApiError, AuthError, Exception):
            # Token expired, but we'll try to refresh on first API call
            return dropbox.Dropbox(DROPBOX_TOKEN, timeout=120, max_retries_on_rate_limit=5)
    
    raise RuntimeError("Unable to get valid Dropbox access token. Need DROPBOX_TOKEN or DROPBOX_REFRESH_TOKEN")

# Configure Dropbox client with automatic refresh
DBX = get_dropbox_client()
SHOPIFY_GRAPHQL = f"https://{SHOPIFY_SHOP}/admin/api/2024-10/graphql.json"
HDR = {"X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN, "Content-Type": "application/json"}

# Global lock for token refresh
_token_refresh_lock = threading.Lock()

def refresh_dbx_if_needed():
    """Refresh Dropbox client if token is expired"""
    global DBX
    with _token_refresh_lock:
        try:
            # Quick test to see if token works
            DBX.users_get_current_account()
        except AuthError as e:
            # Check if it's an expired token error
            error_str = str(e).lower()
            error_reason = None
            if hasattr(e, 'error'):
                if hasattr(e.error, 'error'):
                    error_reason = str(e.error.error).lower()
                elif hasattr(e.error, 'reason'):
                    error_reason = str(e.error.reason).lower()
            
            if 'expired' in error_str or 'expired_access_token' in error_str or (error_reason and 'expired' in error_reason):
                print("🔄 Dropbox token expired, refreshing...")
                new_client = get_dropbox_client()
                if new_client:
                    DBX = new_client
                    print("✅ Dropbox token refreshed successfully")
                else:
                    print("⚠️  Failed to refresh Dropbox token")
        except Exception:
            pass  # Other errors, don't refresh

def handle_dropbox_auth_error(func):
    """Decorator to automatically refresh token on auth errors"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AuthError as e:
            error_str = str(e).lower()
            if 'expired' in error_str or 'expired_access_token' in error_str:
                refresh_dbx_if_needed()
                # Retry once after refresh
                return func(*args, **kwargs)
            raise
    return wrapper

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

# Global variable to cache the detected team folder base path
_detected_team_base: Optional[str] = None

# GUI callback support (optional)
gui_callbacks = {
    'order_changed': None,
    'scan_detected': None,
    'upload_started': None,
    'upload_progress': None,
    'upload_completed': None,
    'error': None,
    'status': None,
}

# Shopify functions
@retry(
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((requests.exceptions.HTTPError, requests.exceptions.ConnectionError))
)
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
                            firstName
                            lastName
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


def order_update_note(order_gid: str, note: str, append: bool = True) -> bool:
    """Update order note in Shopify. If append=True, appends to existing note."""
    try:
        # First, get current note if appending
        if append:
            query = """
            query($id: ID!) {
                order(id: $id) {
                    id
                    note
                }
            }"""
            result = shopify_gql(query, {"id": order_gid})
            current_note = result.get("order", {}).get("note") or ""
            # Append new note with separator if current note exists
            if current_note:
                note = f"{current_note}\n{note}"
        
        # Update the order note
        mutation = """
        mutation orderUpdate($input: OrderInput!) {
            orderUpdate(input: $input) {
                order { id note }
                userErrors { field message }
            }
        }"""
        result = shopify_gql(mutation, {
            "input": {
                "id": order_gid,
                "note": note
            }
        })
        
        # Check for user errors
        errors = result.get("orderUpdate", {}).get("userErrors", [])
        if errors:
            print("⚠️  Failed to update order note:")
            for err in errors:
                field = err.get("field", [])
                if isinstance(field, list):
                    field = ".".join(field)
                print(f"   Field: {field or 'unknown'} | Message: {err.get('message', 'Unknown error')}")
            return False
        
        return True
    except Exception as e:
        print(f"⚠️  Error updating order note: {e}")
        return False

def get_existing_twin_checks_from_dropbox(order_path: str) -> List[str]:
    """Get list of existing twin check folder names from Dropbox for an order.
    Returns empty list if path doesn't exist or on error."""
    if not order_path:
        return []
    
    twin_checks = []
    try:
        refresh_dbx_if_needed()
        # List folders under the order path
        result = DBX.files_list_folder(order_path)
        
        # Extract folder names (twin check numbers)
        entries = result.entries
        for entry in entries:
            if isinstance(entry, dropbox.files.FolderMetadata):
                # Folder name is the twin check number
                twin_checks.append(entry.name)
        
        # Handle pagination if there are more entries
        while result.has_more:
            result = DBX.files_list_folder_continue(result.cursor)
            entries = result.entries
            for entry in entries:
                if isinstance(entry, dropbox.files.FolderMetadata):
                    twin_checks.append(entry.name)
        
    except ApiError as e:
        # Folder might not exist yet or other API error - that's okay
        # Just return empty list - no existing twin checks
        error_msg = str(e).lower()
        if "not_found" not in error_msg and "not found" not in error_msg:
            # Only log if it's not a simple "not found" error
            print(f"⚠️  Could not list existing twin checks from Dropbox: {e}")
    except Exception as e:
        # Any other error - log but don't fail
        print(f"⚠️  Error getting existing twin checks from Dropbox: {e}")
    
    return twin_checks


def order_add_tags(order_gid: str, tags: List[str]) -> bool:
    """Add tags to an order using Shopify GraphQL. Also appends twin check numbers to order notes."""
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
    
    # Append twin check numbers to order notes
    with order_lock:
        order = current_order_data
        if order and isinstance(order, dict) and order.get("order_gid") == order_gid:
            # Get existing twin checks from current session
            current_twin_checks = order.get("twin_checks", [])
            
            # Get existing twin checks from Dropbox for this order
            order_path = order.get("dropbox_order_path")
            existing_twin_checks = []
            if order_path:
                existing_twin_checks = get_existing_twin_checks_from_dropbox(order_path)
            
            # Combine both lists and remove duplicates
            all_twin_checks = list(set(current_twin_checks + existing_twin_checks))
            
            if all_twin_checks:
                # Sort and format twin checks
                twin_checks_sorted = sorted(all_twin_checks)
                twin_checks_str = ", ".join(twin_checks_sorted)
                note_text = f"Twin Checks: {twin_checks_str}"
                
                if order_update_note(order_gid, note_text, append=True):
                    print(f"📝 Added twin checks to order notes: {twin_checks_str}")
                    # Clear twin checks after adding to notes
                    order["twin_checks"] = []
                else:
                    print(f"⚠️  Failed to add twin checks to order notes")
    
    return True


# Dropbox helpers (mirroring create_customer_dropbox)
def _extract_rate_limit_error(e: Exception) -> Optional[RateLimitError]:
    """Extract RateLimitError from ApiError or nested structures."""
    if isinstance(e, RateLimitError):
        return e
    if isinstance(e, ApiError) and hasattr(e, 'error'):
        error_obj = e.error
        if isinstance(error_obj, RateLimitError):
            return error_obj
        # Handle nested RateLimitError (e.g., RateLimitError containing another RateLimitError)
        if hasattr(error_obj, 'error') and isinstance(error_obj.error, RateLimitError):
            return error_obj.error
    # Check if error message contains rate limit indicators
    error_str = str(e).lower()
    if 'rate' in error_str and 'limit' in error_str:
        # Return the original error if it appears to be rate limit related
        if isinstance(e, RateLimitError):
            return e
    return None

def _format_rate_limit_message(error: Exception) -> Tuple[str, str]:
    """Format a user-friendly rate limit error message.
    Returns (short_message, detail_message) tuple."""
    error_str = str(error)
    
    # Check for specific rate limit reasons
    if 'too_many_write_operations' in error_str:
        short_msg = "Too many write operations"
        detail_msg = "Dropbox is limiting uploads due to too many simultaneous write operations. Waiting before retry..."
    elif 'rate' in error_str.lower() and 'limit' in error_str.lower():
        short_msg = "Rate limit exceeded"
        detail_msg = "Dropbox rate limit hit. Waiting before retry..."
    else:
        short_msg = "Rate limit error"
        detail_msg = f"Dropbox rate limit: {error_str}"
    
    return short_msg, detail_msg

@retry(wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(5), retry=retry_if_exception_type((RateLimitError, ApiError)))
def ensure_folder(path: str) -> None:
    """Create a folder with retry logic for rate limits."""
    try:
        # Skip token refresh - will happen automatically on auth error if needed
        # This makes folder creation much faster
        DBX.files_create_folder_v2(path, autorename=False)
    except (ApiError, RateLimitError) as e:
        # Extract RateLimitError if nested
        rate_limit_err = _extract_rate_limit_error(e)
        if rate_limit_err:
            raise rate_limit_err
        # Swallow 'already exists' or race conditions for other errors
        pass
    except AuthError:
        # Token expired - refresh and retry once
        refresh_dbx_if_needed()
        try:
            DBX.files_create_folder_v2(path, autorename=False)
        except ApiError:
            pass  # Already exists or other error


def ensure_tree(full_path: str) -> None:
    """Create folder tree - refresh token once at the start for efficiency"""
    if not full_path or full_path == "/":
        return

    # Refresh token once at the start instead of before each folder creation
    refresh_dbx_if_needed()

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
    """Create or retrieve shared link - refresh token once"""
    refresh_dbx_if_needed()  # Refresh once for both operations
    try:
        return DBX.sharing_create_shared_link_with_settings(path).url
    except ApiError:
        try:
            links = DBX.sharing_list_shared_links(path=path).links
            return links[0].url if links else None
        except ApiError as e:
            print(f"⚠️  Could not retrieve shared link for {path}: {e}")
            log_dropbox_error("Retrieve Shared Link", e, f"Path: {path}")
            return None


def _create_link_and_update_shopify_background(root_path: str, customer_gid: str, email: str):
    """Background task to create shared link and update Shopify metafield.
    This runs in a separate thread and doesn't block folder creation."""
    try:
        link = make_shared_link(root_path)
        if link and customer_gid:
            if set_customer_dropbox_link(customer_gid, link):
                print(f"💾 Shopify metafield updated for {email}")
            else:
                print(f"⚠️  Metafield update failed for {email}; please verify in Shopify.")
        elif customer_gid and not link:
            print(f"⚠️  Could not create shared link for {root_path}; Shopify metafield not updated.")
    except Exception as e:
        # Log error but don't crash - folders are already created
        error_msg = f"Background task failed for {email}: {e}"
        print(f"⚠️  {error_msg}")
        log_dropbox_error("Background Link Creation", e, f"Root path: {root_path}, Email: {email}")


def ensure_customer_order_folder(order_node: Dict[str, Any]) -> Tuple[str, str]:
    """Create customer and order folders. Returns immediately after folder creation.
    Shared link creation and Shopify update happen in background for new customers."""
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
        # Customer has a metafield link - use DROPBOX_ROOT directly (skip slow existence check)
        # If folder doesn't exist, ensure_tree will create it safely
        root_path = f"{DROPBOX_ROOT}/{email}"
        ensure_tree(root_path)  # Safe - won't overwrite if exists

    # Fallback: default to standard DROPBOX_ROOT/email
    if not root_path:
        # Use DROPBOX_ROOT directly from .env file (simple approach like old code)
        root_path = f"{DROPBOX_ROOT}/{email}"
        ensure_tree(root_path)
        
        # Queue background task to create shared link and update Shopify (non-blocking)
        # Folders are already created, so uploads can proceed immediately
        if customer_gid:
            thread = threading.Thread(
                target=_create_link_and_update_shopify_background,
                args=(root_path, customer_gid, email),
                daemon=True,  # Daemon thread won't prevent program exit
                name=f"LinkCreation-{email}"
            )
            thread.start()
            print(f"📁 Folders created for {email}, creating shared link in background...")

    order_number = (order_node.get("name") or "").replace('#', '').strip()
    if not order_number:
        order_number = ''.join(ch for ch in (order_node.get("name") or "") if ch.isdigit()) or "order"

    order_path = f"{root_path}/{order_number}"
    # Skip the existence check - just create the folder (ensure_tree handles "already exists" gracefully)
    # This is much faster - one API call instead of two
    ensure_tree(order_path)
    print(f"📁 Order folder ready: {order_path}")

    return root_path, order_path

# File operations
def _is_ready(path: Path) -> bool:
    """Check if a directory is ready for processing"""
    try:
        files = list(path.glob("**/*"))
        if not files:
            msg = f"  ⚠️  {path.name} has no files yet"
            print(msg)
            if gui_callbacks['status']:
                gui_callbacks['status'](msg)
            return False
        
        # Check if files are still being written
        file_files = [f for f in files if f.is_file()]
        if not file_files:
            msg = f"  ⚠️  {path.name} has no actual files (only directories)"
            print(msg)
            if gui_callbacks['status']:
                gui_callbacks['status'](msg)
            return False
        
        mtime = max(f.stat().st_mtime for f in file_files)
        time_since_mod = time.time() - mtime
        if time_since_mod <= SETTLE_SECONDS:
            msg = f"  ⏳ {path.name} files still settling ({time_since_mod:.1f}s < {SETTLE_SECONDS}s)"
            print(msg)
            if gui_callbacks['status']:
                gui_callbacks['status'](msg)
            return False
        
        msg = f"  ✅ {path.name} is ready ({len(file_files)} files, {time_since_mod:.1f}s since last write)"
        print(msg)
        if gui_callbacks['status']:
            gui_callbacks['status'](msg)
        return True
    except Exception as e:
        print(f"  ❌ Error checking {path.name}: {e}")
        return False

@retry(wait=wait_exponential(multiplier=2, min=2, max=60), stop=stop_after_attempt(5), retry=retry_if_exception_type((RateLimitError, ApiError, AuthError)))
def _upload_single_file(file_path: Path, dropbox_file: str) -> bool:
    """Upload a single file with retry logic for rate limits."""
    try:
        # Skip token refresh - will happen automatically on auth error if needed
        # This makes uploads much faster
        with open(file_path, "rb") as f:
            DBX.files_upload(f.read(), dropbox_file, mode=WriteMode.overwrite)
        return True
    except AuthError as e:
        # Token expired - refresh and retry
        error_str = str(e).lower()
        if 'expired' in error_str or 'expired_access_token' in error_str:
            refresh_dbx_if_needed()
            # Retry once after refresh
            with open(file_path, "rb") as f:
                DBX.files_upload(f.read(), dropbox_file, mode=WriteMode.overwrite)
            return True
        raise
    except (ApiError, RateLimitError) as e:
        # Extract RateLimitError if nested
        rate_limit_err = _extract_rate_limit_error(e)
        if rate_limit_err:
            log_dropbox_error("Upload Single File", e, f"File: {file_path}, Dropbox path: {dropbox_file}")
            raise rate_limit_err
        # Log ApiErrors before retry
        log_dropbox_error("Upload Single File", e, f"File: {file_path}, Dropbox path: {dropbox_file}")
        # Re-raise other ApiErrors to trigger retry
        raise

def upload_folder(local_dir: Path, dropbox_path: str, progress_callback=None) -> int:
    """Upload a folder to Dropbox with rate limiting"""
    count = 0
    total_files = 0
    # Delay between uploads to prevent rate limiting (in seconds)
    # Very small delay for maximum speed - retry logic handles any rate limits
    UPLOAD_DELAY = 0.05  # 50ms delay - very fast, retry logic handles rate limits if they occur
    
    try:
        # Refresh token once at the start for efficiency
        refresh_dbx_if_needed()
        # Ensure target directory exists
        try:
            DBX.files_create_folder_v2(dropbox_path)
        except ApiError:
            pass
        except AuthError:
            # Token expired - refresh and retry
            refresh_dbx_if_needed()
            try:
                DBX.files_create_folder_v2(dropbox_path)
            except ApiError:
                pass

        # Collect all files to upload
        files_to_upload = []
        for file_path in local_dir.rglob("*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(local_dir)
            dropbox_file = f"{dropbox_path}/{rel_path}"
            files_to_upload.append((file_path, dropbox_file))
        
        # Add WPPC.jpg to the count
        wppc_path = Path(__file__).parent / "WPPC.jpg"
        if wppc_path.exists():
            total_files = len(files_to_upload) + 1
        else:
            total_files = len(files_to_upload)
        
        if progress_callback:
            progress_callback(0, total_files, "Starting upload...")
        
        # Upload files sequentially with delay to prevent rate limits
        for idx, (file_path, dropbox_file) in enumerate(files_to_upload):
            try:
                if progress_callback:
                    progress_callback(idx, total_files, f"Uploading {file_path.name}...")
                if _upload_single_file(file_path, dropbox_file):
                    count += 1
                    # Add delay after successful upload to prevent rate limiting
                    # Skip delay on last file to finish faster
                    if idx < len(files_to_upload) - 1:
                        time.sleep(UPLOAD_DELAY)
            except (RateLimitError, ApiError, AuthError) as e:
                # Extract rate limit error details
                rate_limit_err = _extract_rate_limit_error(e)
                if rate_limit_err:
                    # Rate limit hit - wait longer before continuing
                    error_msg = f"⚠️  Rate limit error uploading {file_path.name} - waiting before retry..."
                    print(error_msg)
                    if progress_callback:
                        progress_callback(idx, total_files, error_msg)
                    log_dropbox_error("Upload File (Rate Limit)", e, f"File: {file_path}, Dropbox path: {dropbox_file}")
                    # Wait longer for rate limits (15-30 seconds)
                    wait_time = 15
                    # Try to extract retry_after if available
                    if hasattr(rate_limit_err, 'retry_after') and rate_limit_err.retry_after is not None:
                        try:
                            wait_time = max(wait_time, int(rate_limit_err.retry_after))
                        except (ValueError, TypeError):
                            pass
                    print(f"   Waiting {wait_time} seconds before continuing...")
                    if progress_callback:
                        progress_callback(idx, total_files, f"Rate limit - waiting {wait_time}s...")
                    time.sleep(wait_time)
                    # Retry the upload after waiting
                    try:
                        if progress_callback:
                            progress_callback(idx, total_files, f"Retrying {file_path.name}...")
                        if _upload_single_file(file_path, dropbox_file):
                            count += 1
                            if idx < len(files_to_upload) - 1:
                                time.sleep(UPLOAD_DELAY)
                        else:
                            error_msg = f"⚠️  Failed to upload {file_path.name} after rate limit wait"
                            print(error_msg)
                            if progress_callback:
                                progress_callback(idx + 1, total_files, error_msg)
                    except Exception as retry_e:
                        error_msg = f"⚠️  Error uploading {file_path.name} after rate limit retry: {retry_e}"
                        print(error_msg)
                        log_dropbox_error("Upload File (Rate Limit Retry Failed)", retry_e, f"File: {file_path}, Dropbox path: {dropbox_file}")
                        if progress_callback:
                            progress_callback(idx + 1, total_files, error_msg)
                else:
                    # Other API error after retries exhausted
                    error_msg = f"⚠️  Error uploading {file_path.name} after retries: {e}"
                    print(error_msg)
                    log_dropbox_error("Upload File (After Retries)", e, f"File: {file_path}, Dropbox path: {dropbox_file}")
                    if progress_callback:
                        progress_callback(idx + 1, total_files, error_msg)
            except Exception as e:
                error_msg = f"Error uploading {file_path}: {e}"
                print(error_msg)
                log_dropbox_error("Upload File (Exception)", e, f"File: {file_path}, Dropbox path: {dropbox_file}")
                if progress_callback:
                    progress_callback(idx + 1, total_files, error_msg)
        
        # Upload WPPC.jpg as the last file in the folder
        if wppc_path.exists():
            wppc_dropbox_path = f"{dropbox_path}/WPPC.jpg"
            try:
                if progress_callback:
                    progress_callback(len(files_to_upload), total_files, "Uploading WPPC.jpg...")
                if _upload_single_file(wppc_path, wppc_dropbox_path):
                    count += 1
                    print(f"✅ Added WPPC.jpg to folder")
            except (RateLimitError, ApiError) as e:
                rate_limit_err = _extract_rate_limit_error(e)
                if rate_limit_err:
                    error_msg = f"⚠️  Rate limit error uploading WPPC.jpg - waiting before retry..."
                    print(error_msg)
                    if progress_callback:
                        progress_callback(len(files_to_upload), total_files, error_msg)
                    log_dropbox_error("Upload WPPC.jpg (Rate Limit)", e, f"Dropbox path: {wppc_dropbox_path}")
                    wait_time = 15
                    if hasattr(rate_limit_err, 'retry_after') and rate_limit_err.retry_after is not None:
                        try:
                            wait_time = max(wait_time, int(rate_limit_err.retry_after))
                        except (ValueError, TypeError):
                            pass
                    print(f"   Waiting {wait_time} seconds before retrying WPPC.jpg...")
                    if progress_callback:
                        progress_callback(len(files_to_upload), total_files, f"Rate limit - waiting {wait_time}s...")
                    time.sleep(wait_time)
                    # Retry WPPC.jpg upload
                    try:
                        if progress_callback:
                            progress_callback(len(files_to_upload), total_files, "Retrying WPPC.jpg...")
                        if _upload_single_file(wppc_path, wppc_dropbox_path):
                            count += 1
                            print(f"✅ Added WPPC.jpg to folder (after retry)")
                    except Exception as retry_e:
                        error_msg = f"⚠️  Failed to upload WPPC.jpg after rate limit retry: {retry_e}"
                        print(error_msg)
                        log_dropbox_error("Upload WPPC.jpg (Rate Limit Retry Failed)", retry_e, f"Dropbox path: {wppc_dropbox_path}")
                        if progress_callback:
                            progress_callback(total_files, total_files, error_msg)
                else:
                    error_msg = f"⚠️  Rate limit error uploading WPPC.jpg after retries: {e}"
                    print(error_msg)
                    log_dropbox_error("Upload WPPC.jpg (After Retries)", e, f"Dropbox path: {wppc_dropbox_path}")
                    if progress_callback:
                        progress_callback(total_files, total_files, error_msg)
            except Exception as e:
                error_msg = f"⚠️  Error uploading WPPC.jpg: {e}"
                print(error_msg)
                log_dropbox_error("Upload WPPC.jpg (Exception)", e, f"Dropbox path: {wppc_dropbox_path}")
                if progress_callback:
                    progress_callback(total_files, total_files, error_msg)
        else:
            print(f"⚠️  WPPC.jpg not found at {wppc_path}")
        
        if progress_callback:
            progress_callback(total_files, total_files, f"✅ Uploaded {count} files")
                
    except Exception as e:
        error_msg = f"❌ Error processing folder {local_dir}: {e}"
        print(error_msg)
        log_dropbox_error("Upload Folder (Exception)", e, f"Folder: {local_dir}, Dropbox path: {dropbox_path}")
        if progress_callback:
            progress_callback(0, 0, error_msg)
    
    return count

def set_order_gui(order_num_raw: str, tags: Optional[List[str]] = None) -> bool:
    """Set the order from GUI (non-interactive version)"""
    global current_order_data
    
    if not order_num_raw or order_num_raw.lower() == "stage":
        with order_lock:
            current_order_data = {"mode": "stage"}
        if gui_callbacks['order_changed']:
            gui_callbacks['order_changed'](current_order_data)
        return True
    
    # Parse combined input like '136720s' -> order '136720' and tag 's'
    order_num = order_num_raw
    parsed_tags: List[str] = []
    m = re.match(r"^#?(\d+)(.*)$", order_num_raw)
    if m:
        order_num = m.group(1)
        trailing = (m.group(2) or "").strip()
        if trailing:
            trailing = trailing.lstrip(' ,')
            parsed_tags = [t.strip() for t in re.split(r"[,\s]+", trailing) if t.strip()]
    
    # Use provided tags or parsed tags
    if tags is not None:
        parsed_tags = tags
    
    # Apply pending tags to previous order
    with order_lock:
        prev = current_order_data
    if prev and isinstance(prev, dict) and prev.get("pending_tags"):
        pending = list(prev.get("pending_tags", []))
        prev_gid = prev.get("order_gid")
        prev_no = prev.get("order_no")
        if prev_gid and pending:
            try:
                order_add_tags(prev_gid, pending)
            except Exception as e:
                print(f"⚠️ Error applying pending tags to {prev_no}: {e}")
            finally:
                with order_lock:
                    if isinstance(current_order_data, dict) and current_order_data.get("order_gid") == prev_gid:
                        current_order_data.pop("pending_tags", None)
    
    # Search for order
    results = shopify_search_orders(f"name:{order_num}")
    if not results:
        return False
    
    # Auto-select first match
    order = results[0]
    tags_confirmed: List[str] = parsed_tags
    
    # Strip # from order name if present
    order_no = order["name"]
    if order_no.startswith("#"):
        order_no = order_no[1:]
    
    # Update GUI immediately with order info (before slow Dropbox operations)
    with order_lock:
        current_order_data = {
            "order_gid": order["id"],
            "order_no": order_no,
            "email": order.get("email", "unknown"),
            "order_node": order,
            "dropbox_root_path": None,  # Will be set after Dropbox operations
            "dropbox_order_path": None,  # Will be set after Dropbox operations
            "twin_checks": []  # Track scan folder names (twin checks) for this order
        }
        if tags_confirmed:
            current_order_data["pending_tags"] = tags_confirmed
    
    # Notify GUI immediately (shows order number right away)
    if gui_callbacks['order_changed']:
        gui_callbacks['order_changed'](current_order_data)
    
    # Do Dropbox operations in background thread (don't block)
    def setup_dropbox_folders():
        try:
            root_path, order_path = ensure_customer_order_folder(order)
            # Update with actual paths
            with order_lock:
                if current_order_data:
                    current_order_data["dropbox_root_path"] = root_path
                    current_order_data["dropbox_order_path"] = order_path
            # Notify GUI again with complete info
            if gui_callbacks['order_changed']:
                gui_callbacks['order_changed'](current_order_data)
        except Exception as exc:
            error_msg = f"⚠️  Error preparing Dropbox folders: {exc}"
            print(error_msg)
            if gui_callbacks['error']:
                gui_callbacks['error']("Order Setup", f"Dropbox folder creation failed: {exc}\nUsing /pending folder instead.")
            root_path, order_path = f"{DROPBOX_ROOT}/pending", f"{DROPBOX_ROOT}/pending"
            # Update with fallback paths
            with order_lock:
                if current_order_data:
                    current_order_data["dropbox_root_path"] = root_path
                    current_order_data["dropbox_order_path"] = order_path
            if gui_callbacks['order_changed']:
                gui_callbacks['order_changed'](current_order_data)
    
    # Run Dropbox setup in background thread
    dropbox_thread = threading.Thread(target=setup_dropbox_folders, daemon=True)
    dropbox_thread.start()
    
    return True

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

        # Strip # from order name if present
        order_no = order["name"]
        if order_no.startswith("#"):
            order_no = order_no[1:]
        
        with order_lock:
            current_order_data = {
                "order_gid": order["id"],
                "order_no": order_no,
                "email": order.get("email", "unknown"),
                "order_node": order,
                "dropbox_root_path": root_path,
                "dropbox_order_path": order_path,
                "twin_checks": []  # Track scan folder names (twin checks) for this order
            }
            if tags_confirmed:
                current_order_data["pending_tags"] = tags_confirmed
        
        # Notify GUI of order change
        if gui_callbacks['order_changed']:
            gui_callbacks['order_changed'](current_order_data)
        
        print(f"✅ Set to order #{order_no}")
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
    
    # Notify GUI of scan detection
    if gui_callbacks['scan_detected']:
        gui_callbacks['scan_detected'](scan_name, order)
    
    # Extract order number from the order dict (captured at upload start time)
    # This ensures we use the order that was active when upload started, not when it completed
    order_no = None
    if order.get("mode") == "stage":
        order_no = "STAGING"
    else:
        order_no = order.get("order_no", "")
        if order_no and order_no.startswith("#"):
            order_no = order_no[1:]
    
    # Upload based on order
    dest = None  # Initialize dest for error handling
    progress_cb = None  # Initialize progress_cb for error handling
    
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
        
        # Notify GUI that upload is starting (pass order_no from the order used for upload)
        if gui_callbacks['upload_started']:
            gui_callbacks['upload_started'](scan_name, dest, order_no)
        
        # Create progress callback if GUI is active
        if gui_callbacks['upload_progress']:
            def progress(current, total, message):
                gui_callbacks['upload_progress'](scan_name, current, total, message)
            progress_cb = progress
        
        uploaded = upload_folder(scan_dir, dest, progress_cb)
        
        # Check if upload was successful (some files uploaded) or completely failed
        if uploaded == 0:
            error_msg = f"⚠️  No files uploaded for {scan_name}. Check logs for details."
            print(error_msg)
            if progress_cb:
                progress_cb(0, 0, error_msg)
            if gui_callbacks['error']:
                gui_callbacks['error'](scan_name, error_msg)
            # Don't mark as processed if nothing was uploaded
            return
        
        print(f"✅ Uploaded {uploaded} files")
        
        # Notify GUI that upload is complete (pass order_no from the order used for upload)
        if gui_callbacks['upload_completed']:
            gui_callbacks['upload_completed'](scan_name, uploaded, dest, order_no)
        else:
            print(f"[DEBUG] upload_completed callback is None!")
        
        # Track twin check (scan folder name) for this order
        if order.get("mode") != "stage":
            order_gid = order.get("order_gid")
            if order_gid:
                with order_lock:
                    if current_order_data and isinstance(current_order_data, dict):
                        if current_order_data.get("order_gid") == order_gid:
                            # Add twin check to list if not already there
                            twin_checks = current_order_data.setdefault("twin_checks", [])
                            if scan_name not in twin_checks:
                                twin_checks.append(scan_name)
        
        # Mark as processed
        STATE[scan_name] = True
        save_state(STATE)
        
    except RateLimitError as e:
        # Extract rate limit details
        rate_limit_err = _extract_rate_limit_error(e) if not isinstance(e, RateLimitError) else e
        error_details = str(e)
        
        # Check if it's a "too_many_write_operations" error
        if 'too_many_write_operations' in error_details:
            error_msg = f"❌ Rate limit error: Too many write operations for {scan_name}"
            detail_msg = "Dropbox is limiting uploads due to too many simultaneous operations. Waiting before retry..."
        else:
            error_msg = f"❌ Rate limit error processing {scan_name}"
            detail_msg = f"Dropbox rate limit hit: {error_details}"
        
        print(error_msg)
        print(f"   {detail_msg}")
        
        # Determine wait time
        wait_time = 30  # Default longer wait for rate limits
        if rate_limit_err and hasattr(rate_limit_err, 'retry_after') and rate_limit_err.retry_after is not None:
            try:
                wait_time = max(wait_time, int(rate_limit_err.retry_after))
            except (ValueError, TypeError):
                pass
        
        print(f"   Waiting {wait_time} seconds before retrying...")
        
        # Log the error
        log_dropbox_error("Process Scan (Rate Limit)", e, f"Scan: {scan_name}, Destination: {dest or 'unknown'}")
        
        # Notify GUI through error callback
        if gui_callbacks['error']:
            gui_callbacks['error'](scan_name, f"{error_msg} - {detail_msg}")
        # Also notify through progress callback if available
        if progress_cb:
            progress_cb(0, 0, f"Rate limit - waiting {wait_time}s before retry...")
        
        time.sleep(wait_time)
        # Don't mark as processed, so it will be retried
    except Exception as e:
        # Check if it's a nested RateLimitError
        rate_limit_err = _extract_rate_limit_error(e)
        if rate_limit_err:
            # Handle as rate limit error
            error_details = str(e)
            if 'too_many_write_operations' in error_details:
                error_msg = f"❌ Rate limit error: Too many write operations for {scan_name}"
                detail_msg = "Dropbox is limiting uploads. The upload will be retried automatically."
            else:
                error_msg = f"❌ Rate limit error processing {scan_name}"
                detail_msg = f"Error: {error_details}"
            
            print(error_msg)
            print(f"   {detail_msg}")
            
            wait_time = 30
            if hasattr(rate_limit_err, 'retry_after') and rate_limit_err.retry_after is not None:
                try:
                    wait_time = max(wait_time, int(rate_limit_err.retry_after))
                except (ValueError, TypeError):
                    pass
            
            print(f"   Waiting {wait_time} seconds before retrying...")
            log_dropbox_error("Process Scan (Rate Limit - Nested)", rate_limit_err, f"Scan: {scan_name}, Destination: {dest or 'unknown'}")
            
            if gui_callbacks['error']:
                gui_callbacks['error'](scan_name, f"{error_msg} - {detail_msg}")
            if progress_cb:
                progress_cb(0, 0, f"Rate limit - waiting {wait_time}s...")
            
            time.sleep(wait_time)
            return  # Don't mark as processed
        
        # Regular exception handling
        error_msg = f"❌ Error processing {scan_name}: {e}"
        print(error_msg)
        
        # Only log Dropbox-related errors to the Dropbox log
        if isinstance(e, (ApiError, RateLimitError, AuthError)):
            log_dropbox_error("Process Scan (Exception)", e, f"Scan: {scan_name}, Destination: {dest or 'unknown'}")
        
        if gui_callbacks['error']:
            gui_callbacks['error'](scan_name, error_msg)
        if progress_cb:
            progress_cb(0, 0, error_msg)

def main():
    print("\n" + "="*60)
    print("📷 DIRECT SCANNER ROUTER")
    print("="*60)
    print(f"Watching: {NORITSU_ROOT}")
    print(f"📄 Dropbox errors will be logged to: {DROPBOX_ERROR_LOG_FILE}")
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