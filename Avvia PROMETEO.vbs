' ═══════════════════════════════════════════════════════════════
' PROMETEO — Avvio silenzioso (nessuna finestra terminale visibile)
' Fai doppio click su questo file per avviare l'app.
' ═══════════════════════════════════════════════════════════════
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
strDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & strDir & "\launcher.ps1""", 0, False
Set WshShell = Nothing
Set FSO = Nothing
