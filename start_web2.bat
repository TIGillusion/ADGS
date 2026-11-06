@echo off
cd /d "%~dp0"
start "" http://127.0.0.1:8021/web2/
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" web_app.py
) else (
    python web_app.py
)
pause
