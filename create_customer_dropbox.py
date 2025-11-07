#!/usr/bin/env python3
"""
Create Dropbox folder for customer and link it to their Shopify profile.

Usage:
    python create_customer_dropbox.py
    # Enter order number or email when prompted
"""

import os
import time
import traceback
from pathlib import PurePosixPath
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv
import requests

import dropbox
from dropbox.exceptions import ApiError

# =================== ENV ===================
load_dotenv()

SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/Orders")
# Note: You can override these in .env file, but default is custom_fields.dropbox
CUSTOMER_LINK_FIELD_NS = os.getenv("CUSTOMER_LINK_FIELD_NS", "custom_fields")
CUSTOMER_LINK_FIELD_KEY = os.getenv("CUSTOMER_LINK_FIELD_KEY", "dropbox")

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and DROPBOX_TOKEN, \
    "Missing required .env entries (SHOPIFY_SHOP, SHOPIFY_ADMIN_TOKEN, DROPBOX_TOKEN)."

# =================== SETUP ===================
DBX = dropbox.Dropbox(DROPBOX_TOKEN, timeout=120, max_retries_on_rate_limit=5)
SHOPIFY_GRAPHQL = f"https://{SHOPIFY_SHOP}/admin/api/2024-10/graphql.json"
HDR = {"X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN, "Content-Type": "application/json"}

# =================== SHOPIFY ===================
def shopify_gql(query: str, variables=None) -> Dict[str, Any]:
    r = requests.post(SHOPIFY_GRAPHQL, headers=HDR, json={"query": query, "variables": variables or {}}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "errors" in data or data.get("data") is None:
        raise RuntimeError(f"Shopify GraphQL error: {data}")
    return data["data"]

def shopify_search_orders(q: str) -> List[Dict[str, Any]]:
    """Search for orders by order number or query."""
    query = f"""
    query($q: String!) {{
        orders(first: 10, query: $q, sortKey: CREATED_AT, reverse: true) {{
            edges {{
                node {{
                    id
                    name
                    email
                    customer {{
                        id
                        email
                        displayName
                        metafield(namespace: "{CUSTOMER_LINK_FIELD_NS}", key: "{CUSTOMER_LINK_FIELD_KEY}") {{
                            value
                        }}
                    }}
                }}
            }}
        }}
    }}"""
    data = shopify_gql(query, {"q": q})
    return [e["node"] for e in data["orders"]["edges"]]

def shopify_search_customers_by_email(email: str) -> List[Dict[str, Any]]:
    """Search for customers by email address."""
    query = f"""
    query($query: String!) {{
        customers(first: 10, query: $query) {{
            edges {{
                node {{
                    id
                    email
                    displayName
                    metafield(namespace: "{CUSTOMER_LINK_FIELD_NS}", key: "{CUSTOMER_LINK_FIELD_KEY}") {{
                        value
                    }}
                }}
            }}
        }}
    }}"""
    data = shopify_gql(query, {
        "query": f"email:{email}"
    })
    return [e["node"] for e in data["customers"]["edges"]]

def verify_customer_metafield(customer_gid: str) -> Optional[str]:
    """Verify the metafield was set by querying it back."""
    query = f"""
    query($id: ID!) {{
        customer(id: $id) {{
            id
            metafield(namespace: "{CUSTOMER_LINK_FIELD_NS}", key: "{CUSTOMER_LINK_FIELD_KEY}") {{
                value
            }}
        }}
    }}"""
    try:
        data = shopify_gql(query, {"id": customer_gid})
        metafield = data.get("customer", {}).get("metafield")
        return metafield.get("value") if metafield else None
    except Exception:
        return None

def get_metafield_definition() -> Optional[Dict[str, Any]]:
    """Get the metafield definition to see what type it expects."""
    query = f"""
    query {{
        metafieldDefinitions(first: 250, ownerType: CUSTOMER) {{
            edges {{
                node {{
                    namespace
                    key
                    type {{
                        name
                    }}
                    access {{
                        admin
                    }}
                }}
            }}
            pageInfo {{
                hasNextPage
            }}
        }}
    }}"""
    try:
        data = shopify_gql(query)
        definitions = data.get("metafieldDefinitions", {}).get("edges", [])
        has_next = data.get("metafieldDefinitions", {}).get("pageInfo", {}).get("hasNextPage", False)
        
        if has_next:
            print(f"   ⚠️  Note: There are more than 250 metafield definitions (pagination needed)")
        
        # Debug: show some definitions to help troubleshoot
        print(f"   🔍 Found {len(definitions)} metafield definitions")
        matching_defs = []
        for edge in definitions:
            node = edge.get("node", {})
            if (node.get("namespace") == CUSTOMER_LINK_FIELD_NS and 
                node.get("key") == CUSTOMER_LINK_FIELD_KEY):
                matching_defs.append(node)
        
        if matching_defs:
            return matching_defs[0]
        
        # If not found, show what we did find (first few with similar namespace/key)
        if len(definitions) > 0:
            print(f"   📋 Sample definitions found:")
            for i, edge in enumerate(definitions[:5]):
                node = edge.get("node", {})
                print(f"      {i+1}. {node.get('namespace')}.{node.get('key')} (type: {node.get('type', {}).get('name', 'unknown')})")
        
        return None
    except Exception as e:
        print(f"   ⚠️  Could not query metafield definition: {e}")
        traceback.print_exc()
        return None

def set_customer_dropbox_link_via_customer_update(customer_gid: str, url: str) -> bool:
    """Try setting metafield via customerUpdate mutation."""
    mutation = """
    mutation customerUpdate($input: CustomerInput!) {
        customerUpdate(input: $input) {
            customer {
                id
                metafield(namespace: $namespace, key: $key) {
                    value
                }
            }
            userErrors {
                field
                message
            }
        }
    }"""
    
    try:
        result = shopify_gql(mutation, {
            "input": {
                "id": customer_gid,
                "metafields": [{
                    "namespace": CUSTOMER_LINK_FIELD_NS,
                    "key": CUSTOMER_LINK_FIELD_KEY,
                    "value": url
                }]
            },
            "namespace": CUSTOMER_LINK_FIELD_NS,
            "key": CUSTOMER_LINK_FIELD_KEY
        })
        
        errors = result.get("customerUpdate", {}).get("userErrors", [])
        if errors:
            print(f"   Errors from customerUpdate:")
            for error in errors:
                print(f"      - {error.get('field', 'unknown')}: {error.get('message', 'unknown error')}")
            return False
        
        customer = result.get("customerUpdate", {}).get("customer")
        if customer:
            metafield = customer.get("metafield")
            if metafield and metafield.get("value") == url:
                return True
        return False
    except Exception as e:
        print(f"   ❌ customerUpdate failed: {e}")
        return False

def set_customer_dropbox_link(customer_gid: str, url: str) -> bool:
    """Set the Dropbox link metafield on a customer's Shopify profile.
    Returns True if successful, False otherwise."""
    mutation = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
        metafieldsSet(metafields: $metafields) {
            metafields {
                id
                namespace
                key
                value
            }
            userErrors {
                field
                message
            }
        }
    }"""
    
    # Check if metafield definition exists and auto-detect correct one
    print(f"   🔍 Checking metafield definitions...")
    definition = get_metafield_definition()
    
    # Also check if custom_fields.dropbox exists (common alternative)
    query_all = f"""
    query {{
        metafieldDefinitions(first: 250, ownerType: CUSTOMER) {{
            edges {{
                node {{
                    namespace
                    key
                    type {{
                        name
                    }}
                }}
            }}
        }}
    }}"""
    try:
        all_data = shopify_gql(query_all)
        all_defs = all_data.get("metafieldDefinitions", {}).get("edges", [])
        
        # Check for custom_fields.dropbox
        for edge in all_defs:
            node = edge.get("node", {})
            if node.get("namespace") == "custom_fields" and node.get("key") == "dropbox":
                if CUSTOMER_LINK_FIELD_NS != "custom_fields" or CUSTOMER_LINK_FIELD_KEY != "dropbox":
                    print(f"   ⚠️  WARNING: Found 'custom_fields.dropbox' but using '{CUSTOMER_LINK_FIELD_NS}.{CUSTOMER_LINK_FIELD_KEY}'")
                    print(f"      This will save to the wrong metafield!")
                    print(f"      To fix: Update your .env file:")
                    print(f"        CUSTOMER_LINK_FIELD_NS=custom_fields")
                    print(f"        CUSTOMER_LINK_FIELD_KEY=dropbox")
                    print(f"      Or remove those lines to use the default")
    except:
        pass
    
    if definition:
        mf_type = definition.get("type", {}).get("name", "unknown")
        access = definition.get("access", {}).get("admin", "unknown")
        print(f"   ✅ Found metafield definition: type = {mf_type}, admin access = {access}")
    else:
        print(f"   ⚠️  Metafield definition '{CUSTOMER_LINK_FIELD_NS}.{CUSTOMER_LINK_FIELD_KEY}' not found via query")
        print(f"      (Will try to set it anyway - if it fails, the metafield may need to be created)")
    
    # Try different approaches
    attempts = [
        # Try 1: Without type field (if definition exists, Shopify should use it)
        {"name": "metafieldsSet without type", "input": {
            "ownerId": customer_gid,
            "namespace": CUSTOMER_LINK_FIELD_NS,
            "key": CUSTOMER_LINK_FIELD_KEY,
            "value": url
        }},
        # Try 2: With url type
        {"name": "metafieldsSet with url type", "input": {
            "ownerId": customer_gid,
            "namespace": CUSTOMER_LINK_FIELD_NS,
            "key": CUSTOMER_LINK_FIELD_KEY,
            "type": "url",
            "value": url
        }},
        # Try 3: With single_line_text_field type
        {"name": "metafieldsSet with single_line_text_field type", "input": {
            "ownerId": customer_gid,
            "namespace": CUSTOMER_LINK_FIELD_NS,
            "key": CUSTOMER_LINK_FIELD_KEY,
            "type": "single_line_text_field",
            "value": url
        }},
    ]
    
    for attempt in attempts:
        print(f"\n   📤 Attempt {attempt['name']}...")
        try:
            result = shopify_gql(mutation, {"metafields": [attempt["input"]]})
        except Exception as e:
            print(f"   ❌ API call failed: {e}")
            traceback.print_exc()
            continue
        
        metafields_set = result.get("metafieldsSet", {})
        errors = metafields_set.get("userErrors", [])
        returned_metafields = metafields_set.get("metafields", [])
        
        # Print full response for debugging
        print(f"   🔍 Response: {len(errors)} errors, {len(returned_metafields)} metafields returned")
        if errors:
            print(f"   ❌ Errors:")
            for error in errors:
                field = error.get('field', ['unknown'])[0] if isinstance(error.get('field'), list) else error.get('field', 'unknown')
                message = error.get('message', 'unknown error')
                print(f"      Field: {field}")
                print(f"      Message: {message}")
        if returned_metafields:
            mf = returned_metafields[0]
            print(f"   ✅ Metafield returned: {mf.get('namespace')}.{mf.get('key')} = {str(mf.get('value', ''))[:50]}...")
        
        # If successful (no errors and metafield returned), verify it
        if not errors and returned_metafields:
            print(f"   🔍 Verifying metafield was saved...")
            time.sleep(3)  # Give Shopify more time to process
            
            saved_value = verify_customer_metafield(customer_gid)
            if saved_value:
                if saved_value == url:
                    print(f"   ✅ Verification successful! Metafield value matches.")
                    return True
                else:
                    print(f"   ⚠️  Metafield exists but value differs:")
                    print(f"      Expected: {url[:60]}...")
                    print(f"      Got:      {saved_value[:60]}...")
                    return False
            else:
                print(f"   ⚠️  Mutation succeeded but metafield not found on query")
                print(f"      Full API response was: {result}")
                # Sometimes there's a delay, return True if mutation succeeded
                return True
        elif not errors:
            # No errors but also no metafield returned - might still be OK
            print(f"   ℹ️  No errors, but no metafield returned in response")
            print(f"      Full API response: {result}")
            time.sleep(3)
            saved_value = verify_customer_metafield(customer_gid)
            if saved_value == url:
                print(f"   ✅ Verification successful after delay!")
                return True
    
    # Try customerUpdate as last resort
    print(f"\n   📤 Trying alternative: customerUpdate mutation...")
    if set_customer_dropbox_link_via_customer_update(customer_gid, url):
        print(f"   ✅ customerUpdate succeeded!")
        time.sleep(2)
        saved_value = verify_customer_metafield(customer_gid)
        if saved_value == url:
            return True
    
    # All attempts failed
    print(f"\n   ❌ All attempts to set metafield failed")
    print(f"\n   🔍 Debugging info:")
    print(f"      Customer ID: {customer_gid}")
    print(f"      Namespace: {CUSTOMER_LINK_FIELD_NS}")
    print(f"      Key: {CUSTOMER_LINK_FIELD_KEY}")
    print(f"      URL: {url[:80]}...")
    print(f"\n   💡 Next steps:")
    print(f"      1. Verify metafield definition in Shopify Admin:")
    print(f"         Settings → Custom data → Customers")
    print(f"         Must match: {CUSTOMER_LINK_FIELD_NS}.{CUSTOMER_LINK_FIELD_KEY}")
    print(f"      2. Check metafield is 'Visible in admin'")
    print(f"      3. Verify API token has write_customers scope")
    print(f"      4. Try manually setting the value in Shopify to test")
    print(f"      5. Check if other customers have this metafield working")
    return False

# =================== DROPBOX ===================
def ensure_folder(path: str) -> None:
    """Create a single folder; ignore 'already exists' and races."""
    try:
        DBX.files_create_folder_v2(path, autorename=False)
    except ApiError:
        pass  # Already exists or race condition, that's fine

def ensure_tree(full_path: str) -> None:
    """Create every component of the given POSIX path if missing."""
    if not full_path or full_path == "/":
        return
    
    parts = [p for p in PurePosixPath(full_path).parts if p != "/"]
    if not parts:
        return
    
    # Try to create the entire path at once first
    try:
        ensure_folder(full_path)
        return
    except:
        pass
    
    # Fall back to creating each path component
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        ensure_folder(cur)

def make_shared_link(path: str) -> Optional[str]:
    """Create or retrieve a shared link for a Dropbox path."""
    try:
        return DBX.sharing_create_shared_link_with_settings(path).url
    except ApiError:
        # Link already exists, get existing one
        links = DBX.sharing_list_shared_links(path=path).links
        return links[0].url if links else None

# =================== MAIN ===================
def get_email_from_order(order_input: str) -> Optional[tuple]:
    """Look up email from order number.

    Returns (email, customer_node, order_number_digits) or None.
    """
    order_input = order_input.strip()
    
    # Build search query
    if order_input.isdigit():
        q = f"name:{order_input} OR order_number:{order_input}"
    else:
        q = f"name:{order_input}"
    
    print(f"\n🔍 Searching for order: {order_input}")
    orders = shopify_search_orders(q)
    
    if not orders:
        print(f"❌ No orders found matching: {order_input}")
        return None
    
    # If multiple orders found, let user pick
    if len(orders) > 1:
        print(f"\n📋 Found {len(orders)} orders:")
        for i, order in enumerate(orders, 1):
            order_no = order.get("name", "Unknown")
            email = (order.get("customer") or {}).get("email") or order.get("email") or "unknown"
            print(f"{i:>2}) Order #{order_no:<10} Email: {email}")
        
        pick = input("\n👉 Pick an order number (or Enter to use first): ").strip()
        if pick.isdigit():
            idx = int(pick) - 1
            if 0 <= idx < len(orders):
                order = orders[idx]
            else:
                print("❌ Invalid number, using first order")
                order = orders[0]
        else:
            order = orders[0]
    else:
        order = orders[0]
    
    # Extract email and customer info
    customer = order.get("customer") or {}
    email = (customer.get("email") or order.get("email") or "").strip().lower()
    
    if not email:
        print("❌ No email found for this order")
        return None
    
    order_no = order.get("name", "Unknown")
    order_digits = ''.join(ch for ch in order_no if ch.isdigit())
    if not order_digits:
        order_digits = ''.join(ch for ch in order_input if ch.isdigit())
    print(f"\n✅ Found Order #{order_no}")
    print(f"   Email: {email}")
    
    return (email, customer, order_digits)

def create_customer_dropbox(email_or_order: str) -> None:
    """Create Dropbox folder for email and link it to Shopify customer profile.
    
    Can accept either an email address or an order number.
    """
    email_or_order = email_or_order.strip()
    
    # Determine if input is email or order number
    order_folder_number: Optional[str] = None

    if '@' in email_or_order:
        # Treat as email
        email = email_or_order.lower()
        customer_node = None
    else:
        # Treat as order number - look up email
        result = get_email_from_order(email_or_order)
        if not result:
            return
        email, customer_node, order_folder_number = result
    
    email = email.strip().lower()
    
    print(f"\n📧 Looking up customer: {email}")
    
    # Search for customer in Shopify
    customers = shopify_search_customers_by_email(email)
    if not customers:
        print(f"❌ No customer found in Shopify with email: {email}")
        print("   You can still create the Dropbox folder, but it won't be linked to Shopify.")
        response = input("   Create folder anyway? (y/n): ").strip().lower()
        if response != 'y':
            return
        
        # Create folder without linking
        root_path = f"{DROPBOX_ROOT}/{email}"
        ensure_tree(root_path)
        link = make_shared_link(root_path)
        if link:
            print(f"✅ Created Dropbox folder: {root_path}")
            print(f"📎 Shared link: {link}")
        else:
            print(f"❌ Created folder but could not create shared link")
        return
    
    # If multiple customers found, let user pick
    if len(customers) > 1:
        print(f"\n📋 Found {len(customers)} customers with this email:")
        for i, customer in enumerate(customers, 1):
            name = customer.get("displayName") or "Unknown"
            existing_link = (customer.get("metafield") or {}).get("value")
            status = f"[Has link: {existing_link[:50]}...]" if existing_link else "[No link]"
            print(f"{i:>2}) {name:<30} {status}")
        
        pick = input("\n👉 Pick a customer number (or Enter to use first): ").strip()
        if pick.isdigit():
            idx = int(pick) - 1
            if 0 <= idx < len(customers):
                customer = customers[idx]
            else:
                print("❌ Invalid number, using first customer")
                customer = customers[0]
        else:
            customer = customers[0]
    else:
        customer = customers[0]
    
    customer_gid = customer.get("id")
    customer_name = customer.get("displayName") or email
    existing_link = (customer.get("metafield") or {}).get("value")
    
    print(f"\n👤 Customer: {customer_name}")
    
    if existing_link:
        print(f"⚠️  Customer already has a Dropbox link: {existing_link}")
        response = input("   Overwrite with new folder? (y/n): ").strip().lower()
        if response != 'y':
            print("   Cancelled.")
            return
    
    # Create Dropbox folder
    root_path = f"{DROPBOX_ROOT}/{email}"
    print(f"\n📁 Creating Dropbox folder: {root_path}")

    # Check if folder exists and prompt for confirmation
    try:
        DBX.files_get_metadata(root_path)
        print("⚠️  This folder already exists!")
        confirm1 = input("Do you want to override? (y/n): ").strip().lower()
        if confirm1 == 'y':
            print("⚠️  Type 'y' again to confirm override")
            confirm2 = input("Are you really sure? (y/n): ").strip().lower()
            if confirm2 != 'y':
                print("Operation cancelled.")
                return
        else:
            print("Operation cancelled.")
            return
    except ApiError:
        pass  # Folder doesn't exist, proceed as normal
    
    ensure_tree(root_path)

    if order_folder_number:
        order_folder_path = f"{root_path}/{order_folder_number}"
        try:
            DBX.files_get_metadata(order_folder_path)
            print(f"⚠️  Order folder already exists: {order_folder_path}")
        except ApiError:
            ensure_tree(order_folder_path)
            print(f"📁 Created order folder: {order_folder_path}")
    
    # Create shared link
    print("🔗 Creating shared link...")
    link = make_shared_link(root_path)
    
    if not link:
        print("❌ Failed to create shared link")
        return
    
    # Save link to customer profile
    print("💾 Saving link to Shopify customer profile...")
    print(f"   Customer ID: {customer_gid}")
    print(f"   Using metafield: {CUSTOMER_LINK_FIELD_NS}.{CUSTOMER_LINK_FIELD_KEY}")
    print(f"   (If this is wrong, check your .env file or update CUSTOMER_LINK_FIELD_NS/KEY)")
    
    try:
        success = set_customer_dropbox_link(customer_gid, link)
        if success:
            print(f"\n✅ Success!")
            print(f"   Dropbox folder: {root_path}")
            print(f"   Shared link: {link}")
            print(f"   Linked to: {customer_name} ({email})")
            print(f"\n💡 If the link doesn't appear in Shopify immediately:")
            print(f"   1. Refresh the customer page")
            print(f"   2. Check that the metafield '{CUSTOMER_LINK_FIELD_NS}.{CUSTOMER_LINK_FIELD_KEY}' exists")
            print(f"   3. Verify the customer ID is correct")
        else:
            print(f"\n⚠️  Folder and link created, but failed to save to Shopify")
            print(f"   Dropbox folder: {root_path}")
            print(f"   Shared link: {link}")
            print(f"\n💡 Try manually setting the metafield in Shopify:")
            print(f"   - Namespace: {CUSTOMER_LINK_FIELD_NS}")
            print(f"   - Key: {CUSTOMER_LINK_FIELD_KEY}")
            print(f"   - Value: {link}")
    except Exception as e:
        print(f"\n⚠️  Folder and link created, but error saving to Shopify:")
        print(f"   Error: {e}")
        import traceback
        traceback.print_exc()
        print(f"\n   Dropbox folder: {root_path}")
        print(f"   Shared link: {link}")
        print(f"\n💡 Try manually setting the metafield in Shopify:")
        print(f"   - Namespace: {CUSTOMER_LINK_FIELD_NS}")
        print(f"   - Key: {CUSTOMER_LINK_FIELD_KEY}")
        print(f"   - Value: {link}")

def main():
    print("="*60)
    print("Create Customer Dropbox Folder")
    print("="*60)
    print("\n💡 You can enter either:")
    print("   • An order number (e.g., 1234)")
    print("   • An email address (e.g., customer@example.com)")
    
    while True:
        user_input = input("\n📧 Enter order number or email (or 'q' to quit): ").strip()
        
        if not user_input or user_input.lower() == 'q':
            print("👋 Goodbye!")
            break
        
        try:
            create_customer_dropbox(user_input)
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()

