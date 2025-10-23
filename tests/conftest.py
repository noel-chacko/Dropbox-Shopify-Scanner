#!/usr/bin/env python3
"""
Pytest configuration and shared fixtures for testing.
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def temp_dir():
    """Provide a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def mock_env():
    """Mock environment variables for testing."""
    env_vars = {
        "SHOPIFY_SHOP": "test-shop.myshopify.com",
        "SHOPIFY_ADMIN_TOKEN": "shpat_test_token",
        "DROPBOX_TOKEN": "sl.test_token",
        "DROPBOX_ROOT": "/Store/orders",
        "NORITSU_ROOT": "/tmp/test_noritsu",
        "LAB_NAME": "TestLab",
        "SETTLE_SECONDS": "5",
        "AUTO_TAG_S": "true",
        "CUSTOMER_LINK_FIELD_NS": "custom",
        "CUSTOMER_LINK_FIELD_KEY": "dropbox_root_url"
    }
    
    with patch.dict(os.environ, env_vars, clear=True):
        yield env_vars


@pytest.fixture
def sample_order_data():
    """Sample order data for testing."""
    return {
        "id": "gid://shopify/Order/123",
        "orderNumber": 100,
        "email": "test@example.com",
        "displayFulfillmentStatus": "fulfilled",
        "customer": {
            "id": "gid://shopify/Customer/456",
            "email": "test@example.com",
            "displayName": "Test User",
            "metafield": None
        }
    }


@pytest.fixture
def sample_customer_data():
    """Sample customer data for testing."""
    return {
        "id": "gid://shopify/Customer/456",
        "email": "test@example.com",
        "displayName": "Test User",
        "metafield": {"value": "https://dropbox.com/s/existing"}
    }


@pytest.fixture
def mock_dropbox():
    """Mock Dropbox API for testing."""
    with patch('scanner_router.DBX') as mock_dbx:
        # Configure common mock responses
        mock_dbx.files_create_folder_v2.return_value = None
        mock_dbx.files_upload.return_value = None
        mock_dbx.sharing_create_shared_link_with_settings.return_value.url = "https://dropbox.com/s/test"
        mock_dbx.users_get_current_account.return_value.name.display_name = "Test User"
        mock_dbx.users_get_current_account.return_value.email = "test@dropbox.com"
        
        yield mock_dbx


@pytest.fixture
def mock_shopify():
    """Mock Shopify API for testing."""
    with patch('scanner_router.shopify_gql') as mock_gql:
        # Default successful response
        mock_gql.return_value = {"data": "success"}
        yield mock_gql


@pytest.fixture(autouse=True)
def clean_state_file():
    """Clean up state file before and after each test."""
    from scanner_router import STATE_FILE
    
    # Clean up before test
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    
    yield
    
    # Clean up after test
    if STATE_FILE.exists():
        STATE_FILE.unlink()


@pytest.fixture
def mock_input():
    """Mock user input for CLI testing."""
    with patch('builtins.input') as mock_input:
        yield mock_input


@pytest.fixture
def mock_print():
    """Mock print function to capture output."""
    with patch('builtins.print') as mock_print:
        yield mock_print
