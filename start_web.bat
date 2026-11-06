@echo off
rem ============================================================
rem  ADGS Web UI launcher - chat + live graph + node detail view
rem  Open http://127.0.0.1:8021 in browser after start
rem ============================================================
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" web_app.py
) else (
    python web_app.py
)
pause
