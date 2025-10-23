#!/usr/bin/env python3
"""
End-to-end tests for the complete scanner workflow.
These tests simulate the full process from file detection to upload.
"""

import pytest
import tempfile
import time
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
    pass  # Import will happen in individual tests as needed


class TestCompleteWorkflow:
    """Test the complete scanner workflow."""
    
    @patch('scanner_router.shopify_search_orders')
    @patch('scanner_router.get_or_create_customer_root')
    @patch('scanner_router.upload_folder')
    @patch('scanner_router.order_add_tags')
    @patch('scanner_router.shopify_update_note')
    @patch('builtins.input')
    def test_complete_scan_to_upload_workflow(self, mock_input, mock_update_note, 
                                            mock_add_tags, mock_upload, mock_get_root, mock_search):
        """Test complete workflow from scan detection to upload."""
        
        # Setup mock responses
        mock_search.return_value = [
            {
                "id": "gid://shopify/Order/123",
                "orderNumber": 100,
                "email": "customer@example.com",
                "displayFulfillmentStatus": "fulfilled",
                "customer": {
                    "id": "gid://shopify/Customer/456",
                    "email": "customer@example.com",
                    "displayName": "Customer Name"
                }
            }
        ]
        
        mock_get_root.return_value = ("/Store/orders/customer@example.com", "https://dropbox.com/s/customer")
        mock_upload.return_value = 12  # 12 photos uploaded
        mock_input.return_value = "1"  # Select first order
        
        # Create test directory structure
        with tempfile.TemporaryDirectory() as temp_dir:
            noritsu_root = Path(temp_dir) / "Noritsu"
            date_dir = noritsu_root / "2024-01-15"
            twin_dir = date_dir / "roll_001"
            twin_dir.mkdir(parents=True)
            
            # Create sample photo files
            for i in range(5):
                (twin_dir / f"photo_{i:03d}.jpg").write_bytes(b"fake_image_data")
            
            # Create a few more files to simulate a real scan
            for i in range(5, 12):
                (twin_dir / f"IMG_{i:04d}.jpg").write_bytes(b"fake_image_data")
            
            # Import and run the routing function
            from scanner_router import route_job
            
            with patch('scanner_router.AUTO_TAG_S', True):
                route_job(date_dir, twin_dir)
            
            # Verify all the expected calls were made
            mock_search.assert_called_once_with("email:customer@example.com")
            mock_get_root.assert_called_once()
            mock_upload.assert_called_once()
            mock_add_tags.assert_called_once_with("gid://shopify/Order/123", ["s"])
            mock_update_note.assert_called_once()
            
            # Verify the upload path is correct
            upload_call_args = mock_upload.call_args[0]
            assert upload_call_args[1] == "/Store/orders/customer@example.com/100/roll_001/photos"
    
    @patch('scanner_router.upload_folder')
    @patch('builtins.input')
    def test_staging_workflow(self, mock_input, mock_upload):
        """Test staging workflow when order is uncertain."""
        
        mock_upload.return_value = 8
        mock_input.return_value = "stage"  # Choose to stage
        
        with tempfile.TemporaryDirectory() as temp_dir:
            noritsu_root = Path(temp_dir) / "Noritsu"
            date_dir = noritsu_root / "2024-01-15"
            twin_dir = date_dir / "roll_002"
            twin_dir.mkdir(parents=True)
            
            # Create sample files
            for i in range(8):
                (twin_dir / f"scan_{i:03d}.jpg").write_bytes(b"fake_data")
            
            from scanner_router import route_job
            
            with patch('scanner_router.DROPBOX_ROOT', '/Store/orders'):
                route_job(date_dir, twin_dir)
            
            # Verify upload to staging area
            expected_staging_path = "/Store/orders/_staging/2024-01-15/roll_002/photos"
            mock_upload.assert_called_once_with(twin_dir, expected_staging_path)
    
    @patch('scanner_router.shopify_search_orders')
    @patch('builtins.input')
    def test_order_search_workflow(self, mock_input, mock_search):
        """Test order search and selection workflow."""
        
        # Setup multiple search results
        mock_search.return_value = [
            {
                "id": "gid://shopify/Order/123",
                "orderNumber": 100,
                "email": "customer1@example.com",
                "customer": {"email": "customer1@example.com", "displayName": "Customer One"}
            },
            {
                "id": "gid://shopify/Order/124", 
                "orderNumber": 101,
                "email": "customer2@example.com",
                "customer": {"email": "customer2@example.com", "displayName": "Customer Two"}
            }
        ]
        
        mock_input.side_effect = ["customer@example.com", "2"]  # Search then select second order
        
        from scanner_router import pick_order_cli
        
        result = pick_order_cli()
        
        assert result["mode"] == "assign"
        assert result["order_no"] == "101"
        assert result["email"] == "customer2@example.com"
        mock_search.assert_called_once_with("email:customer@example.com")


class TestFileWatcher:
    """Test file watching and detection."""
    
    def test_file_ready_detection(self):
        """Test that files are detected as ready after settle time."""
        
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_scan"
            test_dir.mkdir()
            
            # Create a file
            test_file = test_dir / "photo.jpg"
            test_file.write_bytes(b"test_data")
            
            from scanner_router import Handler
            handler = Handler()
            
            # Should not be ready immediately
            with patch('scanner_router.SETTLE_SECONDS', 5):
                assert not handler._ready(test_dir)
            
            # Wait a moment and modify the file
            time.sleep(0.1)
            test_file.write_bytes(b"updated_data")
            
            # Still not ready (recent modification)
            with patch('scanner_router.SETTLE_SECONDS', 5):
                assert not handler._ready(test_dir)
            
            # Should be ready with no settle time
            with patch('scanner_router.SETTLE_SECONDS', 0):
                assert handler._ready(test_dir)
    
    @patch('scanner_router.route_job')
    def test_directory_scanning(self, mock_route_job):
        """Test scanning directory structure for ready jobs."""
        
        with tempfile.TemporaryDirectory() as temp_dir:
            noritsu_root = Path(temp_dir) / "Noritsu"
            
            # Create date directories
            date1 = noritsu_root / "2024-01-15"
            date2 = noritsu_root / "2024-01-16"
            date1.mkdir(parents=True)
            date2.mkdir(parents=True)
            
            # Create twin check directories
            roll1 = date1 / "roll_001"
            roll2 = date1 / "roll_002" 
            roll3 = date2 / "roll_001"
            
            for roll_dir in [roll1, roll2, roll3]:
                roll_dir.mkdir()
                # Add some files
                (roll_dir / "photo1.jpg").write_bytes(b"data")
                (roll_dir / "photo2.jpg").write_bytes(b"data")
            
            from scanner_router import Handler
            handler = Handler()
            
            # Mock the state to show roll_001 is already processed
            with patch('scanner_router.STATE', {"2024-01-15/roll_001": True}):
                with patch('scanner_router.SETTLE_SECONDS', 0):  # Make all folders ready
                    handler._scan_tree()
            
            # Should only route roll_002 and roll_001 from date2 (roll_001 from date1 is already processed)
            assert mock_route_job.call_count == 2


class TestErrorRecovery:
    """Test error handling and recovery scenarios."""
    
    @patch('scanner_router.shopify_search_orders')
    @patch('builtins.input')
    def test_shopify_api_failure_recovery(self, mock_input, mock_search):
        """Test recovery from Shopify API failures."""
        
        # First call fails, second succeeds
        mock_search.side_effect = [
            RuntimeError("Shopify API timeout"),
            [
                {
                    "id": "gid://shopify/Order/123",
                    "orderNumber": 100,
                    "email": "customer@example.com",
                    "customer": {"email": "customer@example.com"}
                }
            ]
        ]
        
        mock_input.side_effect = ["customer@example.com", "stage"]  # Will fail first, then stage
        
        from scanner_router import pick_order_cli
        
        # Should handle the error and eventually stage
        result = pick_order_cli()
        
        assert result["mode"] == "stage"
        assert mock_search.call_count == 1  # Only called once before error
    
    @patch('scanner_router.upload_folder')
    @patch('scanner_router.shopify_search_orders')
    @patch('builtins.input')
    def test_dropbox_upload_failure_recovery(self, mock_input, mock_search, mock_upload):
        """Test recovery from Dropbox upload failures."""
        
        mock_search.return_value = [
            {
                "id": "gid://shopify/Order/123",
                "orderNumber": 100,
                "email": "customer@example.com",
                "customer": {"email": "customer@example.com"}
            }
        ]
        
        mock_upload.side_effect = Exception("Dropbox upload failed")
        mock_input.return_value = "1"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            date_dir = Path(temp_dir) / "2024-01-15"
            twin_dir = date_dir / "roll_001"
            twin_dir.mkdir(parents=True)
            
            from scanner_router import route_job
            
            # Should raise the exception (not handle it silently)
            with pytest.raises(Exception, match="Dropbox upload failed"):
                route_job(date_dir, twin_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
