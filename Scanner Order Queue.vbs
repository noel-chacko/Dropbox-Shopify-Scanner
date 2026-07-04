' Scanner Order Queue launcher (Windows counterpart to Scanner Order Queue.app)
' - Runs run_order_queue_gui.bat with the console hidden
' - Refuses to start a second copy
' - Output goes to order_queue_launch.log next to this script

Option Explicit

Dim fso, shell, scriptDir, batPath, logPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = scriptDir & "\run_order_queue_gui.bat"
logPath = scriptDir & "\order_queue_launch.log"

If Not fso.FileExists(batPath) Then
    MsgBox "run_order_queue_gui.bat not found next to this launcher.", vbCritical, "Scanner Order Queue"
    WScript.Quit 1
End If

' Refuse a second copy: look for a python process already running the GUI.
Dim wmi, procs, proc
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery( _
    "SELECT CommandLine FROM Win32_Process WHERE Name LIKE 'py%'")
For Each proc In procs
    If Not IsNull(proc.CommandLine) Then
        If InStr(proc.CommandLine, "scanner_order_queue_gui.py") > 0 Then
            MsgBox "Scanner Order Queue is already running.", vbInformation, "Scanner Order Queue"
            WScript.Quit 0
        End If
    End If
Next

' Launch hidden (window style 0), don't wait; log stdout+stderr.
shell.Run "cmd /c """"" & batPath & """ /quiet > """ & logPath & """ 2>&1""", 0, False
