# Testing Guide for Dropbox + Shopify Scanner

This guide explains how to test your scanner integration system comprehensively.

## 🧪 Types of Tests

### 1. Environment & Configuration Tests
**File:** `test_environment.py`
**Purpose:** Verify all external connections and configuration

```bash
# Test your environment setup
python test_environment.py
```

**What it tests:**
- ✅ All required environment variables are set
- ✅ Shopify API connection and permissions
- ✅ Dropbox API connection and permissions  
- ✅ Noritsu scanner path exists and is readable
- ✅ All Python dependencies are installed

### 2. Unit Tests
**File:** `tests/test_unit.py`
**Purpose:** Test individual functions without external dependencies

```bash
# Run unit tests
python -m pytest tests/test_unit.py -v
```

**What it tests:**
- ✅ State file loading/saving
- ✅ Path building functions
- ✅ Customer root path management
- ✅ CLI interaction logic
- ✅ File system event handling

### 3. Integration Tests
**File:** `tests/test_integration.py`
**Purpose:** Test API interactions with mocked external services

```bash
# Run integration tests
python -m pytest tests/test_integration.py -v
```

**What it tests:**
- ✅ Shopify API calls (search, update, tag)
- ✅ Dropbox API calls (upload, sharing)
- ✅ End-to-end workflow scenarios
- ✅ Error handling for API failures

### 4. End-to-End Tests
**File:** `tests/test_end_to_end.py`
**Purpose:** Test complete workflows from start to finish

```bash
# Run end-to-end tests
python -m pytest tests/test_end_to_end.py -v
```

**What it tests:**
- ✅ Complete scan-to-upload workflow
- ✅ Staging workflow for uncertain orders
- ✅ File watcher and detection
- ✅ Error recovery scenarios

## 🚀 Running Tests

### Quick Start
```bash
# Install dependencies and run all tests
python run_tests.py --type all
```

### Individual Test Types
```bash
# Environment test only
python run_tests.py --type env

# Unit tests only  
python run_tests.py --type unit

# Integration tests only
python run_tests.py --type integration

# End-to-end tests only
python run_tests.py --type all

# With coverage report
python run_tests.py --type coverage

# Code linting
python run_tests.py --type lint
```

### Manual Testing
```bash
# Install dependencies
pip install -r requirements.txt

# Run environment test
python test_environment.py

# Run all pytest tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_unit.py -v

# Run with coverage
coverage run -m pytest tests/
coverage report -m
```

## 🔧 Test Configuration

### Environment Setup
Before running tests, you need to set up your `.env` file:

```bash
# Copy the template
cp env_template.txt .env

# Edit with your real credentials
nano .env
```

**Required for real API tests:**
- `SHOPIFY_SHOP` - Your Shopify store domain
- `SHOPIFY_ADMIN_TOKEN` - Admin API token
- `DROPBOX_TOKEN` - Dropbox API token
- `NORITSU_ROOT` - Path to your scanner output

### Test-Only Mode
Most tests use mocked external services, so they don't require real credentials. Only the environment test (`test_environment.py`) needs real credentials.

## 📊 Test Results

### Expected Output
```
🧪 Dropbox + Shopify Scanner - Test Runner
============================================================

🧪 Environment Configuration Test
============================================================
✅ All tests passed! Your environment is ready.

🧪 Unit Tests
============================================================
tests/test_unit.py::TestStateManagement::test_load_state_new_file PASSED
tests/test_unit.py::TestPathBuilding::test_build_dest_paths_from_root PASSED
...

🧪 Integration Tests  
============================================================
tests/test_integration.py::TestShopifyIntegration::test_shopify_search_orders_success PASSED
...

============================================================
📊 Test Results Summary
============================================================
  ✅ PASS Environment Configuration Test
  ✅ PASS Unit Tests
  ✅ PASS Integration Tests
  ✅ PASS End-to-End Tests

Results: 4/4 tests passed
🎉 All tests passed!
```

## 🐛 Troubleshooting Tests

### Common Issues

**1. Missing Dependencies**
```bash
# Install all dependencies
pip install -r requirements.txt
```

**2. Environment Variables Not Set**
```bash
# Check your .env file exists and has all required variables
python test_environment.py
```

**3. Import Errors**
```bash
# Make sure you're in the project root directory
cd /path/to/Dropbox+Shopify+Scanner
python -m pytest tests/
```

**4. Permission Errors**
```bash
# Make sure the NORITSU_ROOT path exists and is readable
ls -la /path/to/your/noritsu/folder
```

### Test-Specific Issues

**Unit Tests Failing:**
- Usually indicates logic errors in core functions
- Check the specific test output for details
- Mock objects might need adjustment

**Integration Tests Failing:**
- Usually indicates API interaction issues
- Check that mock responses match expected formats
- Verify GraphQL query structures

**End-to-End Tests Failing:**
- Usually indicates workflow issues
- Check file system operations
- Verify state management

## 🔍 Debugging Tests

### Verbose Output
```bash
# Run with verbose output
python -m pytest tests/ -v -s

# Run single test with debug output
python -m pytest tests/test_unit.py::TestStateManagement::test_load_state_new_file -v -s
```

### Test Coverage
```bash
# Generate coverage report
coverage run -m pytest tests/
coverage report -m
coverage html  # Creates htmlcov/index.html
```

### Mock Debugging
```bash
# See what mocks are being called
python -c "
import sys
sys.path.insert(0, '.')
from tests.test_integration import TestShopifyIntegration
import pytest
pytest.main(['tests/test_integration.py::TestShopifyIntegration::test_shopify_search_orders_success', '-v', '-s'])
"
```

## 📈 Continuous Testing

### Pre-commit Testing
```bash
# Quick test before committing
python run_tests.py --type lint
python -m pytest tests/test_unit.py -q
```

### Full Test Suite
```bash
# Run everything before major changes
python run_tests.py --type all
```

### Automated Testing
Consider setting up automated testing in your deployment pipeline:

```yaml
# Example GitHub Actions workflow
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.9
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: python run_tests.py --type unit
```

## 🎯 Testing Best Practices

1. **Run tests frequently** during development
2. **Fix failing tests immediately** - don't let them accumulate
3. **Add tests for new features** as you develop them
4. **Use descriptive test names** that explain what's being tested
5. **Keep tests isolated** - each test should be independent
6. **Mock external services** in unit and integration tests
7. **Test error conditions** not just happy paths
8. **Use real data structures** in your mocks when possible

## 📝 Adding New Tests

When adding new functionality:

1. **Add unit tests** for new functions
2. **Add integration tests** for new API interactions  
3. **Add end-to-end tests** for new workflows
4. **Update existing tests** if behavior changes
5. **Document test scenarios** in this guide

Example new test:
```python
def test_new_feature():
    """Test description of what this tests."""
    # Arrange - set up test data
    test_data = {"key": "value"}
    
    # Act - call the function
    result = my_function(test_data)
    
    # Assert - verify the result
    assert result == expected_value
```

This testing framework ensures your scanner integration is robust and reliable! 🚀
