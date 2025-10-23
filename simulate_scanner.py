#!/usr/bin/env python3
"""
Simulate Scanner - Creates fake scanner folders and files for testing
"""

import os
import time
from pathlib import Path
from datetime import datetime

def create_fake_scan(date_str, roll_name, num_photos=5):
    """Create a fake scanner folder with photos."""
    scan_dir = Path(f"test_noritsu/{date_str}/{roll_name}")
    scan_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📷 Creating fake scan: {date_str}/{roll_name}")
    
    # Create fake photo files
    for i in range(1, num_photos + 1):
        photo_file = scan_dir / f"photo_{i:03d}.jpg"
        photo_file.write_text(f"fake_image_data_{i}")
        print(f"   Created: {photo_file}")
        
        # Small delay to simulate scanner writing files
        time.sleep(0.5)
    
    print(f"✅ Fake scan complete: {num_photos} photos in {date_str}/{roll_name}")
    print(f"   Scanner router should detect this in {3} seconds...")
    return scan_dir

def main():
    print("🎬 Scanner Simulator")
    print("=" * 40)
    print("This creates fake scanner folders to test the scanner router.")
    print("Run this in one terminal, scanner_router.py in another.")
    print()
    
    while True:
        print("\nOptions:")
        print("1. Create fake scan for today")
        print("2. Create fake scan for specific date")
        print("3. Create multiple rolls for same date")
        print("4. Exit")
        
        choice = input("\nChoose option (1-4): ").strip()
        
        if choice == "1":
            today = datetime.now().strftime("%Y-%m-%d")
            roll = input(f"Enter roll name for {today} (e.g., roll_001): ").strip() or "roll_001"
            num = input("Number of photos (default 5): ").strip()
            num = int(num) if num.isdigit() else 5
            
            create_fake_scan(today, roll, num)
            
        elif choice == "2":
            date = input("Enter date (YYYY-MM-DD): ").strip()
            roll = input("Enter roll name (e.g., roll_001): ").strip() or "roll_001"
            num = input("Number of photos (default 5): ").strip()
            num = int(num) if num.isdigit() else 5
            
            create_fake_scan(date, roll, num)
            
        elif choice == "3":
            date = input("Enter date (YYYY-MM-DD): ").strip()
            num_rolls = input("Number of rolls (default 2): ").strip()
            num_rolls = int(num_rolls) if num_rolls.isdigit() else 2
            
            for i in range(1, num_rolls + 1):
                roll = f"roll_{i:03d}"
                num = input(f"Number of photos for {roll} (default 5): ").strip()
                num = int(num) if num.isdigit() else 5
                create_fake_scan(date, roll, num)
                time.sleep(2)  # Delay between rolls
                
        elif choice == "4":
            print("👋 Goodbye!")
            break
            
        else:
            print("❌ Invalid choice")

if __name__ == "__main__":
    main()
