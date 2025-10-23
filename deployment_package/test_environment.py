#!/usr/bin/env python3
"""
Environment and Configuration Test Script
Tests all required environment variables and basic connectivity.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import requests
import dropbox
from dropbox.exceptions import ApiError

def test_env_variables():
    """Test that all required environment variables are set."""
    print("🔧 Testing Environment Variables...")
    
    load_dotenv()
    
    required_vars = [
        "SHOPIFY_SHOP",
        "SHOPIFY_ADMIN_TOKEN", 
        "DROPBOX_TOKEN",
        "NORITSU_ROOT"
    ]
    
    optional_vars = [
        "DROPBOX_ROOT",
        "LAB_NAME",
        "SETTLE_SECONDS",
        "AUTO_TAG_S",
        "CUSTOMER_LINK_FIELD_NS",
        "CUSTOMER_LINK_FIELD_KEY"
    ]
    
    missing = []
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing.append(var)
        else:
            # Mask sensitive values
            display_value = value[:10] + "..." if len(value) > 10 else value
            print(f"  ✓ {var}: {display_value}")
    
    for var in optional_vars:
        value = os.getenv(var)
        if value:
            print(f"  ✓ {var}: {value}")
        else:
            print(f"  - {var}: (not set, using default)")
    
    if missing:
        print(f"  ❌ Missing required variables: {', '.join(missing)}")
        return False
    
    print("  ✅ All required environment variables are set")
    return True

def test_shopify_connection():
    """Test Shopify API connectivity and permissions."""
    print("\n🛍️  Testing Shopify Connection...")
    
    shopify_shop = os.getenv("SHOPIFY_SHOP")
    token = os.getenv("SHOPIFY_ADMIN_TOKEN")
    
    if not shopify_shop or not token:
        print("  ❌ Missing Shopify credentials")
        return False
    
    url = f"https://{shopify_shop}/admin/api/2024-10/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json"
    }
    
    # Test query - get shop info
    query = """
    query {
        shop {
            name
            email
            domain
        }
    }
    """
    
    try:
        response = requests.post(url, headers=headers, json={"query": query}, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if "errors" in data:
            print(f"  ❌ Shopify API Error: {data['errors']}")
            return False
        
        shop_info = data.get("data", {}).get("shop", {})
        print(f"  ✅ Connected to shop: {shop_info.get('name', 'Unknown')}")
        print(f"  ✅ Shop domain: {shop_info.get('domain', 'Unknown')}")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Shopify connection failed: {e}")
        return False

def test_dropbox_connection():
    """Test Dropbox API connectivity and permissions."""
    print("\n📁 Testing Dropbox Connection...")
    
    token = os.getenv("DROPBOX_TOKEN")
    if not token:
        print("  ❌ Missing Dropbox token")
        return False
    
    try:
        dbx = dropbox.Dropbox(token, timeout=30)
        
        # Test basic account info
        account = dbx.users_get_current_account()
        print(f"  ✅ Connected to Dropbox account: {account.name.display_name}")
        print(f"  ✅ Account email: {account.email}")
        
        # Test root folder access
        root_path = os.getenv("DROPBOX_ROOT", "/Store/orders")
        try:
            dbx.files_list_folder(root_path)
            print(f"  ✅ Can access root folder: {root_path}")
        except ApiError as e:
            if e.error.is_path_not_found():
                print(f"  ⚠️  Root folder doesn't exist: {root_path}")
                print("     (This is OK - it will be created automatically)")
            else:
                print(f"  ❌ Cannot access root folder: {e}")
                return False
        
        return True
        
    except Exception as e:
        print(f"  ❌ Dropbox connection failed: {e}")
        return False

def test_noritsu_path():
    """Test that the Noritsu root path exists and is accessible."""
    print("\n📷 Testing Noritsu Path...")
    
    noritsu_root = os.getenv("NORITSU_ROOT")
    if not noritsu_root:
        print("  ❌ NORITSU_ROOT not set")
        return False
    
    path = Path(noritsu_root)
    print(f"  📂 Checking path: {path}")
    
    if not path.exists():
        print(f"  ❌ Path does not exist: {path}")
        return False
    
    if not path.is_dir():
        print(f"  ❌ Path is not a directory: {path}")
        return False
    
    # Check if we can read the directory
    try:
        list(path.iterdir())
        print(f"  ✅ Path is readable")
    except PermissionError:
        print(f"  ❌ Permission denied reading path: {path}")
        return False
    
    # Show some example subdirectories
    subdirs = [d for d in path.iterdir() if d.is_dir()][:5]
    if subdirs:
        print(f"  📁 Found {len(subdirs)} subdirectories (showing first 5):")
        for subdir in subdirs:
            print(f"     - {subdir.name}")
    else:
        print(f"  ⚠️  No subdirectories found (this might be normal)")
    
    return True

def test_dependencies():
    """Test that all required Python packages are installed."""
    print("\n📦 Testing Dependencies...")
    
    required_packages = [
        "dotenv",
        "tenacity", 
        "requests",
        "dropbox",
        "watchdog"
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✅ {package}")
        except ImportError:
            missing.append(package)
            print(f"  ❌ {package}")
    
    if missing:
        print(f"\n  ❌ Missing packages: {', '.join(missing)}")
        print("  Run: pip install -r requirements.txt")
        return False
    
    print("  ✅ All required packages are installed")
    return True

def main():
    """Run all environment tests."""
    print("🧪 Dropbox + Shopify Scanner - Environment Test")
    print("=" * 50)
    
    tests = [
        ("Dependencies", test_dependencies),
        ("Environment Variables", test_env_variables),
        ("Shopify Connection", test_shopify_connection),
        ("Dropbox Connection", test_dropbox_connection),
        ("Noritsu Path", test_noritsu_path),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"  ❌ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("📊 Test Results Summary:")
    
    all_passed = True
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status} {test_name}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 All tests passed! Your environment is ready.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Please fix the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

