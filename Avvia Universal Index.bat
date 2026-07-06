@echo off
rem ============================================================
rem  Universal Index — Avvio del server locale
rem  Doppio clic: avvia il server e apre il browser sull'indice.
rem  La finestra resta aperta (Ctrl+C per fermare il server).
rem ============================================================
cd /d "%~dp0"
python server.py
pause
