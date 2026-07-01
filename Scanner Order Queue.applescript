on run
    -- Resolve the project folder from wherever this app lives
    set appPath to POSIX path of (path to me)
    set appDir to do shell script "dirname " & quoted form of appPath

    set guiFile to appDir & "/scanner_order_queue_gui.py"
    try
        do shell script "test -f " & quoted form of guiFile
    on error
        -- Try parent directory (if app is in a subfolder)
        set guiFile to appDir & "/../scanner_order_queue_gui.py"
        try
            do shell script "test -f " & quoted form of guiFile
            set appDir to do shell script "dirname " & quoted form of guiFile
        on error
            display dialog "Cannot find scanner_order_queue_gui.py

This app needs to be in the project folder alongside:
- scanner_order_queue_gui.py
- scanner_router_direct.py
- .env file
- WPPC.jpg

Current app location: " & appDir buttons {"OK"} default button "OK" with icon stop
            return
        end try
    end try

    -- If the GUI is already running, don't start a second copy
    try
        do shell script "pgrep -f scanner_order_queue_gui.py"
        display notification "Scanner Order Queue is already running" with title "Scanner Order Queue"
        return
    end try

    -- Launch detached via the shell script (handles venv + missing packages);
    -- output goes to order_queue_launch.log next to the project files.
    set logFile to appDir & "/order_queue_launch.log"
    do shell script "cd " & quoted form of appDir & " && nohup /bin/bash ./run_order_queue_gui.sh > " & quoted form of logFile & " 2>&1 &"

    -- Poll for the GUI process to appear. First launch has to import the
    -- Dropbox/Shopify SDKs (and may pip-install), so it can take several
    -- seconds — only treat it as a failure if it never shows up.
    set started to false
    repeat 15 times
        delay 1
        try
            do shell script "pgrep -f scanner_order_queue_gui.py"
            set started to true
            exit repeat
        end try
    end repeat

    if not started then
        set errTail to do shell script "tail -15 " & quoted form of logFile & " 2>/dev/null || echo '(no log)'"
        display dialog "Scanner Order Queue failed to start.

Last lines of the launch log:

" & errTail buttons {"OK"} default button "OK" with icon stop
    end if
end run
