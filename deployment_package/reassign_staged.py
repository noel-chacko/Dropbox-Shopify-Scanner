# reassign_staged.py
# Moves /Store/orders/_staging/<date>/<twin_check> --> /Store/orders/<email>/<order>/<twin_check>
# Also ensures the customer root link exists on the Shopify customer profile.

import os
from typing import Dict, Any, List, Tuple

from dotenv import load_dotenv
import requests
import dropbox
from dropbox.exceptions import ApiError

load_dotenv()

SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/Store/orders")

assert SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN and DROPBOX_TOKEN, "Missing .env"

DBX = dropbox.Dropbox(DROPBOX_TOKEN, timeout=120)
SHOPIFY_GRAPHQL = f"https://{SHOPIFY_SHOP}/admin/api/2024-10/graphql.json"
HDR = {"X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN, "Content-Type": "application/json"}

STAGING_ROOT = f"{DROPBOX_ROOT}/_staging"

def shopify_gql(query: str, variables=None) -> Dict[str, Any]:
    r = requests.post(SHOPIFY_GRAPHQL, headers=HDR, json={"query": query, "variables": variables or {}}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "errors" in data or data.get("data") is None:
        raise RuntimeError(f"Shopify GraphQL error: {data}")
    return data["data"]

def shopify_search_orders(q: str) -> List[Dict[str, Any]]:
    query = """
    query($q:String!){
      orders(first:10, query:$q, sortKey:CREATED_AT, reverse:true){
        edges{
          node{
            id
            orderNumber
            email
            customer{
              id
              email
              displayName
              metafield(namespace:"custom", key:"dropbox_root_url"){ value }
            }
          }
        }
      }
    }"""
    data = shopify_gql(query, {"q": q})
    return [e["node"] for e in data["orders"]["edges"]]

def set_customer_dropbox_link(customer_gid: str, url: str) -> None:
    mutation = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) { userErrors { field message } }
    }"""
    shopify_gql(mutation, {"metafields": [{
        "ownerId": customer_gid,
        "namespace":"custom",
        "key":"dropbox_root_url",
        "type":"url",
        "value": url
    }]})

def list_folder(path: str):
    items = []
    try:
        res = DBX.files_list_folder(path)
    except ApiError:
        return []
    items.extend(res.entries)
    while res.has_more:
        res = DBX.files_list_folder_continue(res.cursor)
        items.extend(res.entries)
    return items

def list_staged_pairs() -> List[str]:
    pairs = []
    for date_md in list_folder(STAGING_ROOT):
        if not isinstance(date_md, dropbox.files.FolderMetadata): continue
        for twin_md in list_folder(f"{STAGING_ROOT}/{date_md.name}"):
            if isinstance(twin_md, dropbox.files.FolderMetadata):
                pairs.append(f"{date_md.name}/{twin_md.name}")
    return sorted(pairs)

def ensure_customer_root_link(customer_gid: str, email: str, existing_link: str | None) -> Tuple[str,str]:
    if existing_link:
        # try resolve; if fails, we'll just proceed
        try:
            meta = DBX.sharing_get_shared_link_metadata(existing_link)
            return meta.path_lower, existing_link
        except Exception:
            pass
    root_path = f"{DROPBOX_ROOT}/{email}"
    try:
        DBX.files_create_folder_v2(root_path)
    except ApiError:
        pass
    # create/fetch link
    try:
        link = DBX.sharing_create_shared_link_with_settings(root_path).url
    except ApiError:
        link = DBX.sharing_list_shared_links(path=root_path).links[0].url
    if customer_gid:
        try:
            set_customer_dropbox_link(customer_gid, link)
        except Exception as e:
            print(f"[WARN] could not save customer link: {e}")
    return root_path, link

def move_staged_to_customer(pair: str, order_node: Dict[str, Any]) -> str:
    date, twin = pair.split("/", 1)
    src = f"{STAGING_ROOT}/{date}/{twin}"
    email = (order_node.get("customer") or {}).get("email") or order_node.get("email") or "unknown"
    dest_root = f"{DROPBOX_ROOT}/{email}/{order_node['orderNumber']}/{twin}"

    # ensure parents exist
    for p in [f"{DROPBOX_ROOT}/{email}", f"{DROPBOX_ROOT}/{email}/{order_node['orderNumber']}"]:
        try: DBX.files_create_folder_v2(p)
        except ApiError: pass

    DBX.files_move_v2(src, dest_root, autorename=False)
    return dest_root

def main():
    pairs = list_staged_pairs()
    if not pairs:
        print("No staged items.")
        return

    print("Staged items:")
    for i, p in enumerate(pairs, 1):
        print(f"{i:>2}) {p}")
    pick = input("Pick staged # to reassign: ").strip()
    if not pick.isdigit() or not (1 <= int(pick) <= len(pairs)):
        print("Invalid selection.")
        return
    pair = pairs[int(pick)-1]

    q = input("Search Shopify (email/name/order#): ").strip()
    if "@" in q:
        q2 = f"email:{q}"
    elif q.isdigit():
        q2 = f"name:{q} OR order_number:{q}"
    else:
        q2 = q

    results = shopify_search_orders(q2)
    if not results:
        print("No order matches.")
        return

    print("Matches:")
    for i, r in enumerate(results, 1):
        who = (r.get("customer") or {}).get("email") or r.get("email") or "unknown"
        print(f"{i:>2})  #{r['orderNumber']:<6}  {who}")
    pick2 = input("Pick order #: ").strip()
    if not pick2.isdigit() or not (1 <= int(pick2) <= len(results)):
        print("Invalid selection.")
        return
    order = results[int(pick2)-1]

    cust = order.get("customer") or {}
    email = cust.get("email") or order.get("email") or "unknown"
    existing = (cust.get("metafield") or {}).get("value")
    ensure_customer_root_link(cust.get("id"), email, existing)

    dest = move_staged_to_customer(pair, order)
    print(f"Moved staged job {pair} → {dest}")

if __name__ == "__main__":
    main()
