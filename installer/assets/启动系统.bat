@echo off
start "" "%~dp0FreightQuoteSystem.exe" --mode supervisor
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:8765/"
