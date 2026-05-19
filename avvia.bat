@echo off
REM ═══════════════════════════════════════════════════════════════
REM PROMETEO — Avvio (preferibilmente usare "Avvia PROMETEO.vbs"
REM per un avvio COMPLETAMENTE silenzioso senza nessuna finestra).
REM ═══════════════════════════════════════════════════════════════
powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
exit
