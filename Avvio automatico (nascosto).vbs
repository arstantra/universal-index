' ============================================================
'  Universal Index — Avvio automatico silenzioso
'  Avvia il server locale in background, senza finestre.
'  Non apre il browser: quando ti serve l'indice, vai su
'  http://localhost:5000 (o usa "Avvia Universal Index.bat").
'
'  PER L'AVVIO AUTOMATICO CON WINDOWS:
'    1. Tasto destro su questo file -> Crea collegamento
'    2. Win+R -> digita: shell:startup -> Invio
'    3. Sposta il collegamento nella cartella che si apre
' ============================================================
Set fso = CreateObject("Scripting.FileSystemObject")
cartella = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = cartella
shell.Run "pythonw ""server.py"" --no-browser", 0, False
