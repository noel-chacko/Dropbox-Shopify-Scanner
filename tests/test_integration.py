#!/usr/bin/env python3
"""
Integration tests for scanner_router.py
Tests API interactions with mocked external services.
"""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add parent directory to path
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
        shopify_search_orders, shopify_update_note, order_add_tags,
        set_customer_dropbox_link, upload_folder, make_shared_link
    )


class TestShopifyIntegration:
    """Test Shopify API interactions."""
    
    @patch('scanner_router.shopify_gql')
    def test_shopify_search_orders_success(self, mock_gql):
        """Test successful order search."""
        mock_gql.return_value = {
            "orders": {
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/Order/123",
                            "orderNumber": 100,
                            "email": "test@example.com",
                            "displayFulfillmentStatus": "fulfilled",
                            "customer": {
                                "id": "gid://shopify/Customer/456",
                                "email": "test@example.com",
                                "displayName": "Test User"
                            }
                        }
                    }
                ]
            }
        }
        
        with patch('scanner_router.CUSTOMER_LINK_FIELD_NS', 'custom'):
            with patch('scanner_router.CUSTOMER_LINK_FIELD_KEY', 'dropbox_root_url'):
                results = shopify_search_orders("test@example.com")
                
                assert len(results) == 1
                assert results[0]["orderNumber"] == 100
                assert results[0]["email"] == "test@example.com"
                mock_gql.assert_called_once()
    
    @patch('scanner_router.shopify_gql')
    def test_shopify_update_note(self, mock_gql):
        """Test updating order note."""
        mock_gql.return_value = {"orderUpdate": {"userErrors": []}}
        
        shopify_update_note("gid://shopify/Order/123", "Test note")
        
        mock_gql.assert_called_once()
        call_args = mock_gql.call_args
        assert "orderUpdate" in call_args[0][0]  # mutation query
    
    @patch('scanner_router.shopify_gql')
    def test_order_add_tags(self, mock_gql):
        """Test adding tags to order."""
        mock_gql.return_value = {"orderTagsAdd": {"userErrors": []}}
        
        order_add_tags("gid://shopify/Order/123", ["s", "processed"])
        
        mock_gql.assert_called_once()
        call_args = mock_gql.call_args
        assert "orderTagsAdd" in call_args[0][0]  # mutation query
    
    @patch('scanner_router.shopify_gql')
    def test_set_customer_dropbox_link(self, mock_gql):
        """Test setting customer metafield."""
        mock_gql.return_value = {"metafieldsSet": {"userErrors": []}}
        
        with patch('scanner_router.CUSTOMER_LINK_FIELD_NS', 'custom'):
            with patch('scanner_router.CUSTOMER_LINK_FIELD_KEY', 'dropbox_root_url'):
                set_customer_dropbox_link("gid://shopify/Customer/456", "https://dropbox.com/s/test")
                
                mock_gql.assert_called_once()
                call_args = mock_gql.call_args
                assert "metafieldsSet" in call_args[0][0]  # mutation query


class TestDropboxIntegration:
    """Test Dropbox API interactions."""
    
    @patch('scanner_router.DBX')
    def test_make_shared_link_new(self, mock_dbx):
        """Test creating new shared link."""
        mock_dbx.sharing_create_shared_link_with_settings.return_value.url = "https://dropbox.com/s/new"
        
        result = make_shared_link("/Store/orders/test")
        
        assert result == "https://dropbox.com/s/new"
        mock_dbx.sharing_create_shared_link_with_settings.assert_called_once_with("/Store/orders/test")
    
    @patch('scanner_router.DBX')
    def test_make_shared_link_existing(self, mock_dbx):
        """Test getting existing shared link."""
        from dropbox.exceptions import ApiError
        mock_dbx.sharing_create_shared_link_with_settings.side_effect = ApiError("", "", "", "")
        
        mock_link = Mock()
        mock_link.url = "https://dropbox.com/s/existing"
        mock_dbx.sharing_list_shared_links.return_value.links = [mock_link]
        
        result = make_shared_link("/Store/orders/test")
        
        assert result == "https://dropbox.com/s/existing"
    
    @patch('scanner_router.ensure_tree')
    @patch('scanner_router.upload_file')
    def test_upload_folder(self, mock_upload, mock_ensure_tree):
        """Test uploading folder contents."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_folder"
            test_dir.mkdir()
            
            # Create test files
            (test_dir / "photo1.jpg").write_text("photo1")
            (test_dir / "photo2.jpg").write_text("photo2")
            (test_dir / "subdir").mkdir()  # Should be ignored
            
            count = upload_folder(test_dir, "/Store/orders/test/photos")
            
            assert count == 2
            assert mock_upload.call_count == 2
            mock_ensure_tree.assert_called_once_with("/Store/orders/test/photos")


class TestEndToEndWorkflow:
    """Test complete workflow scenarios."""
    
    @patch('scanner_router.shopify_search_orders')
    @patch('scanner_router.get_or_create_customer_root')
    @patch('scanner_router.upload_folder')
    @patch('scanner_router.order_add_tags')
    @patch('scanner_router.shopify_update_note')
    def test_route_job_complete_workflow(self, mock_update_note, mock_add_tags, 
                                       mock_upload, mock_get_root, mock_search):
        """Test complete job routing workflow."""
        # Setup mocks
        mock_search.return_value = [
            {
                "id": "gid://shopify/Order/123",
                "orderNumber": 100,
                "email": "test@example.com",
                "displayFulfillmentStatus": "fulfilled",
                "customer": {"email": "test@example.com"}
            }
        ]
        mock_get_root.return_value = ("/Store/orders/test@example.com", "https://dropbox.com/s/test")
        mock_upload.return_value = 5
        
        # Mock input for CLI
        with patch('builtins.input', return_value="1"):
            with tempfile.TemporaryDirectory() as temp_dir:
                date_dir = Path(temp_dir) / "2024-01-01"
                twin_dir = date_dir / "roll1"
                twin_dir.mkdir(parents=True)
                
                # Create test files
                (twin_dir / "photo1.jpg").write_text("photo1")
                (twin_dir / "photo2.jpg").write_text("photo2")
                
                from scanner_router import route_job
                
                with patch('scanner_router.AUTO_TAG_S', True):
                    route_job(date_dir, twin_dir)
                
                # Verify all steps were called
                mock_search.assert_called_once()
                mock_get_root.assert_called_once()
                mock_upload.assert_called_once()
                mock_add_tags.assert_called_once_with("gid://shopify/Order/123", ["s"])
                mock_update_note.assert_called_once()
    
    @patch('scanner_router.upload_folder')
    def test_route_job_staging(self, mock_upload):
        """Test job staging workflow."""
        mock_upload.return_value = 3
        
        with patch('builtins.input', return_value="stage"):
            with tempfile.TemporaryDirectory() as temp_dir:
                date_dir = Path(temp_dir) / "2024-01-01"
                twin_dir = date_dir / "roll1"
                twin_dir.mkdir(parents=True)
                
                from scanner_router import route_job
                
                with patch('scanner_router.DROPBOX_ROOT', '/Store/orders'):
                    route_job(date_dir, twin_dir)
                
                # Should upload to staging area
                expected_staging_path = "/Store/orders/_staging/2024-01-01/roll1/photos"
                mock_upload.assert_called_once_with(twin_dir, expected_staging_path)


class TestErrorHandling:
    """Test error handling scenarios."""
    
    @patch('scanner_router.shopify_gql')
    def test_shopify_api_error(self, mock_gql):
        """Test handling Shopify API errors."""
        mock_gql.side_effect = RuntimeError("Shopify GraphQL error: {'errors': ['Invalid token']}")
        
        with pytest.raises(RuntimeError, match="Shopify GraphQL error"):
            shopify_search_orders("test@example.com")
    
    @patch('scanner_router.DBX')
    def test_dropbox_api_error(self, mock_dbx):
        """Test handling Dropbox API errors."""
        from dropbox.exceptions import ApiError
        mock_dbx.sharing_create_shared_link_with_settings.side_effect = ApiError("", "", "", "")
        mock_dbx.sharing_list_shared_links.return_value.links = []
        
        result = make_shared_link("/Store/orders/test")
        
        assert result is None
    
    @patch('scanner_router.set_customer_dropbox_link')
    @patch('scanner_router.make_shared_link')
    @patch('scanner_router.ensure_tree')
    def test_customer_link_save_error(self, mock_ensure_tree, mock_make_link, mock_set_link):
        """Test handling customer link save errors."""
        mock_make_link.return_value = "https://dropbox.com/s/test"
        mock_set_link.side_effect = Exception("Permission denied")
        
        order_node = {
            "customer": {
                "id": "gid://shopify/Customer/123",
                "email": "test@example.com"
            }
        }
        
        from scanner_router import get_or_create_customer_root
        
        with patch('scanner_router.DROPBOX_ROOT', '/Store/orders'):
            with patch('builtins.print') as mock_print:
                root_path, link = get_or_create_customer_root(order_node)
                
                assert root_path == "/Store/orders/test@example.com"
                assert link == "https://dropbox.com/s/test"
                mock_print.assert_called_once()  # Should print warning


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
