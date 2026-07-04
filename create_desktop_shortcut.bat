@echo off
REM One-time setup: put a "Scanner Order Queue" icon on the Windows Desktop.
REM The shortcut launches "Scanner Order Queue.vbs" (hidden console) with the
REM PL logo icon. Safe to re-run; it just overwrites the shortcut.

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$lnk = $ws.CreateShortcut((Join-Path $desktop 'Scanner Order Queue.lnk'));" ^
  "$lnk.TargetPath = Join-Path '%~dp0' 'Scanner Order Queue.vbs';" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.IconLocation = (Join-Path '%~dp0' 'PL.ico') + ',0';" ^
  "$lnk.Description = 'Scanner Order Queue';" ^
  "$lnk.Save();" ^
  "Write-Host 'Desktop shortcut created:' (Join-Path $desktop 'Scanner Order Queue.lnk')"

if errorlevel 1 (
    echo.
    echo Failed to create the shortcut. See messages above.
) else (
    echo.
    echo Done! Look for "Scanner Order Queue" on your Desktop.
)
pause
