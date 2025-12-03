on run
    -- Get the path to this app
    set appPath to POSIX path of (path to me)
    set appDir to do shell script "dirname " & quoted form of appPath
    
    -- Check if we're in the project folder or if app was moved
    -- Look for scanner_router_gui.py in the same directory as the app
    set guiFile to appDir & "/scanner_router_gui.py"
    
    -- If not found, try looking in common locations
    try
        do shell script "test -f " & quoted form of guiFile
    on error
        -- Try parent directory (if app is in subfolder)
        set guiFile to appDir & "/../scanner_router_gui.py"
        try
            do shell script "test -f " & quoted form of guiFile
            set appDir to do shell script "dirname " & quoted form of guiFile
        on error
            -- File not found - show error
            display dialog "Cannot find scanner_router_gui.py

The app needs to be in the same folder as:
- scanner_router_gui.py
- scanner_router_direct.py
- .env file
- WPPC.jpg

Current app location: " & appDir buttons {"OK"} default button "OK" with icon stop
            return
        end try
    end try
    
    -- Try to find python3 with PySide6 installed
    try
        do shell script "cd " & quoted form of appDir & " && python3 -c 'import PySide6' 2>&1"
        -- If we get here, PySide6 is available
        do shell script "cd " & quoted form of appDir & " && python3 scanner_router_gui.py"
    on error errMsg
        -- PySide6 not found - show helpful error
        display dialog "PySide6 is not installed.

Please install it by running this command in Terminal:

pip3 install PySide6

Or install all requirements:
pip3 install -r requirements.txt

Error: " & errMsg buttons {"OK"} default button "OK" with icon stop
    end try
end run

