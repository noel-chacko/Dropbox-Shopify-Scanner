#!/usr/bin/env python3
"""
Comprehensive test runner for Dropbox + Shopify Scanner
Runs all types of tests with different configurations.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


def run_command(cmd, description):
    """Run a command and return success status."""
    print(f"\n{'='*60}")
    print(f"🧪 {description}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        if result.returncode == 0:
            print(f"✅ {description} - PASSED")
            return True
        else:
            print(f"❌ {description} - FAILED (exit code: {result.returncode})")
            return False
            
    except Exception as e:
        print(f"❌ {description} - ERROR: {e}")
        return False


def install_dependencies():
    """Install test dependencies."""
    print("📦 Installing test dependencies...")
    return run_command("pip install -r requirements.txt", "Dependency Installation")


def run_environment_test():
    """Run the environment configuration test."""
    return run_command("python test_environment.py", "Environment Configuration Test")


def run_unit_tests():
    """Run unit tests."""
    return run_command("python -m pytest tests/test_unit.py -v", "Unit Tests")


def run_integration_tests():
    """Run integration tests."""
    return run_command("python -m pytest tests/test_integration.py -v", "Integration Tests")


def run_all_tests():
    """Run all tests."""
    return run_command("python -m pytest tests/ -v", "All Tests")


def run_coverage():
    """Run tests with coverage report."""
    # Install coverage if not present
    subprocess.run("pip install coverage", shell=True, capture_output=True)
    return run_command(
        "coverage run -m pytest tests/ && coverage report -m && coverage html",
        "Test Coverage Report"
    )


def lint_code():
    """Run code linting."""
    # Install flake8 if not present
    subprocess.run("pip install flake8", shell=True, capture_output=True)
    return run_command("flake8 scanner_router.py test_environment.py", "Code Linting")


def main():
    """Main test runner."""
    parser = argparse.ArgumentParser(description="Test runner for Dropbox + Shopify Scanner")
    parser.add_argument(
        "--type", 
        choices=["env", "unit", "integration", "all", "coverage", "lint", "install"],
        default="all",
        help="Type of tests to run"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    print("🧪 Dropbox + Shopify Scanner - Test Runner")
    print("=" * 60)
    
    results = []
    
    if args.type == "install" or args.type == "all":
        results.append(("Dependencies", install_dependencies()))
    
    if args.type == "env" or args.type == "all":
        results.append(("Environment Test", run_environment_test()))
    
    if args.type == "unit" or args.type == "all":
        results.append(("Unit Tests", run_unit_tests()))
    
    if args.type == "integration" or args.type == "all":
        results.append(("Integration Tests", run_integration_tests()))
    
    if args.type == "coverage":
        results.append(("Coverage Report", run_coverage()))
    
    if args.type == "lint":
        results.append(("Code Linting", lint_code()))
    
    # Print summary
    print(f"\n{'='*60}")
    print("📊 Test Results Summary")
    print(f"{'='*60}")
    
    passed = 0
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
        if success:
            passed += 1
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
