#!/usr/bin/env python3
"""
Unit tests for core functions in scanner_router.py
Tests individual functions without external dependencies.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import sys
import os

# Add parent directory to path so we can import scanner_router
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock environment variables before importing scanner_router
with patch.dict(os.environ, {
    'SHOPIFY_SHOP': 'test-shop.myshopify.com',
    'SHOPIFY_ADMIN_TOKEN': 'shpat_test_token',
    'DROPBOX_TOKEN': 'sl.test_token',
    'NORITSU_ROOT': '/tmp/test_noritsu',
    'DROPBOX_ROOT': '/Store/orders',
    'LAB_NAME': 'TestLab',
    'SETTLE_SECONDS': '5',
    'AUTO_TAG_S': 'true',
    'CUSTOMER_LINK_FIELD_NS': 'custom',
    'CUSTOMER_LINK_FIELD_KEY': 'dropbox_root_url'
}):
    from scanner_router import (
        load_state, save_state, build_dest_paths_from_root,
        get_or_create_customer_root, pick_order_cli
    )


class TestStateManagement:
    """Test state file loading and saving."""
    
    def test_load_state_new_file(self):
        """Test loading state when file doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "test_state.json"
            with patch('scanner_router.STATE_FILE', state_file):
                state = load_state()
                assert state == {}
    
    def test_load_state_existing_file(self):
        """Test loading state from existing file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "test_state.json"
            test_data = {"2024-01-01/test": True, "2024-01-02/test": False}
            state_file.write_text(json.dumps(test_data))
            
            with patch('scanner_router.STATE_FILE', state_file):
                state = load_state()
                assert state == test_data
    
    def test_load_state_corrupted_file(self):
        """Test loading state from corrupted file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "test_state.json"
            state_file.write_text("invalid json")
            
            with patch('scanner_router.STATE_FILE', state_file):
                state = load_state()
                assert state == {}
    
    def test_save_state(self):
        """Test saving state to file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "test_state.json"
            test_data = {"2024-01-01/test": True}
            
            with patch('scanner_router.STATE_FILE', state_file):
                save_state(test_data)
                
                assert state_file.exists()
                loaded_data = json.loads(state_file.read_text())
                assert loaded_data == test_data


class TestPathBuilding:
    """Test path building functions."""
    
    def test_build_dest_paths_from_root(self):
        """Test building destination paths."""
        photos_dir, link_target = build_dest_paths_from_root("/Store/orders/customer@email.com", "100", "roll1")
        
        assert photos_dir == "/Store/orders/customer@email.com/100/roll1/photos"
        assert link_target == "/Store/orders/customer@email.com/100/roll1"
    
    def test_build_dest_paths_with_slashes(self):
        """Test path building with various slash configurations."""
        photos_dir, link_target = build_dest_paths_from_root("/Store/orders/customer@email.com/", "100", "roll1")
        
        # Note: The function creates double slashes, which is expected behavior
        assert photos_dir == "/Store/orders/customer@email.com//100/roll1/photos"
        assert link_target == "/Store/orders/customer@email.com//100/roll1"


class TestCustomerRoot:
    """Test customer root path management."""
    
    @patch('scanner_router.set_customer_dropbox_link')
    @patch('scanner_router.make_shared_link')
    @patch('scanner_router.ensure_tree')
    def test_get_or_create_customer_root_new_customer(self, mock_ensure_tree, mock_make_link, mock_set_link):
        """Test creating customer root for new customer."""
        mock_make_link.return_value = "https://dropbox.com/s/test"
        
        order_node = {
            "customer": {
                "id": "gid://shopify/Customer/123",
                "email": "test@example.com"
            },
            "email": "test@example.com"
        }
        
        with patch('scanner_router.DROPBOX_ROOT', '/Store/orders'):
            root_path, link = get_or_create_customer_root(order_node)
            
            expected_path = "/Store/orders/test@example.com"
            assert root_path == expected_path
            assert link == "https://dropbox.com/s/test"
            mock_ensure_tree.assert_called_once_with(expected_path)
            mock_make_link.assert_called_once_with(expected_path)
            mock_set_link.assert_called_once_with("gid://shopify/Customer/123", "https://dropbox.com/s/test")
    
    @patch('scanner_router.resolve_link_to_path')
    def test_get_or_create_customer_root_existing_link(self, mock_resolve):
        """Test using existing customer link."""
        mock_resolve.return_value = "/Store/orders/test@example.com"
        
        order_node = {
            "customer": {
                "id": "gid://shopify/Customer/123",
                "email": "test@example.com",
                "metafield": {"value": "https://dropbox.com/s/existing"}
            }
        }
        
        root_path, link = get_or_create_customer_root(order_node)
        
        assert root_path == "/Store/orders/test@example.com"
        assert link == "https://dropbox.com/s/existing"
        mock_resolve.assert_called_once_with("https://dropbox.com/s/existing")
    
    def test_get_or_create_customer_root_no_customer(self):
        """Test handling order without customer."""
        order_node = {
            "email": "test@example.com"
        }
        
        with patch('scanner_router.DROPBOX_ROOT', '/Store/orders'):
            with patch('scanner_router.ensure_tree') as mock_ensure:
                with patch('scanner_router.make_shared_link') as mock_link:
                    mock_link.return_value = "https://dropbox.com/s/test"
                    
                    root_path, link = get_or_create_customer_root(order_node)
                    
                    assert root_path == "/Store/orders/test@example.com"
                    assert link == "https://dropbox.com/s/test"


class TestCLI:
    """Test CLI interaction functions."""
    
    @patch('builtins.input')
    @patch('scanner_router.shopify_search_orders')
    def test_pick_order_cli_stage(self, mock_search, mock_input):
        """Test CLI staging option."""
        mock_input.return_value = "stage"
        
        result = pick_order_cli()
        
        assert result == {"mode": "stage"}
        mock_search.assert_not_called()
    
    @patch('builtins.input')
    @patch('scanner_router.shopify_search_orders')
    def test_pick_order_cli_email_search(self, mock_search, mock_input):
        """Test CLI email search."""
        mock_search.return_value = [
            {
                "id": "gid://shopify/Order/123",
                "orderNumber": 100,
                "email": "test@example.com",
                "displayFulfillmentStatus": "fulfilled",
                "customer": {"email": "test@example.com", "displayName": "Test User"}
            }
        ]
        mock_input.side_effect = ["test@example.com", "1"]
        
        result = pick_order_cli()
        
        assert result["mode"] == "assign"
        assert result["order_no"] == "100"
        assert result["email"] == "test@example.com"
        mock_search.assert_called_once_with("email:test@example.com")
    
    @patch('builtins.input')
    @patch('scanner_router.shopify_search_orders')
    def test_pick_order_cli_order_number_search(self, mock_search, mock_input):
        """Test CLI order number search."""
        mock_search.return_value = []
        mock_input.side_effect = ["123", "stage"]
        
        result = pick_order_cli()
        
        assert result == {"mode": "stage"}
        mock_search.assert_called_once_with("name:123 OR order_number:123")
    
    @patch('builtins.input')
    @patch('scanner_router.shopify_search_orders')
    def test_pick_order_cli_name_search(self, mock_search, mock_input):
        """Test CLI name search."""
        mock_search.return_value = []
        mock_input.side_effect = ["John", "stage"]
        
        result = pick_order_cli()
        
        assert result == {"mode": "stage"}
        mock_search.assert_called_once_with("John")


class TestFileSystemHandler:
    """Test file system event handling."""
    
    def test_handler_ready_check(self):
        """Test if folder is ready for processing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_folder"
            test_dir.mkdir()
            
            # Create a test file
            test_file = test_dir / "test.jpg"
            test_file.write_text("test content")
            
            from scanner_router import Handler
            handler = Handler()
            
            # Should not be ready immediately (files too new)
            with patch('scanner_router.SETTLE_SECONDS', 10):
                assert not handler._ready(test_dir)
            
            # Should be ready after settle time
            with patch('scanner_router.SETTLE_SECONDS', 0):
                assert handler._ready(test_dir)
    
    def test_handler_ready_empty_folder(self):
        """Test ready check for empty folder."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "empty_folder"
            test_dir.mkdir()
            
            from scanner_router import Handler
            handler = Handler()
            
            assert not handler._ready(test_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
