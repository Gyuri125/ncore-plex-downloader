Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = baseDir

If fso.FileExists(baseDir & "\.venv\Scripts\pythonw.exe") Then
    WshShell.Run chr(34) & baseDir & "\.venv\Scripts\pythonw.exe" & chr(34) & " " & chr(34) & baseDir & "\launcher.py" & chr(34), 0, False
ElseIf fso.FileExists(baseDir & "\Inditas.bat") Then
    WshShell.Run chr(34) & baseDir & "\Inditas.bat" & chr(34), 0, False
Else
    WshShell.Run "pythonw.exe launcher.py", 0, False
End If
