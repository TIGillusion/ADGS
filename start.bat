@echo off
rem ============================================================
rem  ADGS v3 launcher - one-click start (double-click to run)
rem  Priority: .venv -> system python (with deps) -> one-click setup
rem  - .venv exists: use it
rem  - else system python already has deps (openai/yaml/numpy): use it
rem  - else run setup_new_pc.bat (auto-installs Python / Hermes CLI / deps)
rem  - Extra args are passed through to main.py (e.g. -d 3)
rem ============================================================
cd /d "%~dp0"

set "PYTHON_BIN="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_BIN=.venv\Scripts\python.exe"
    echo [ADGS] Using .venv python.
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import openai, yaml, numpy" >nul 2>nul
        if not errorlevel 1 (
            set "PYTHON_BIN=python"
            echo [ADGS] No .venv, but system python has all deps - using it.
        )
    )
)

if "%PYTHON_BIN%"=="" (
    echo.
    echo [ADGS] No usable python found. Running one-click setup first...
    echo [ADGS] Will install: Python 3.13 / Hermes Agent CLI / dependencies
    echo [ADGS] First install takes ~10-20 min. Keep network up, do NOT close window.
    echo.
    call "%~dp0setup_new_pc.bat"
    if errorlevel 1 (
        echo.
        echo [ADGS] Setup did not complete. Fix the errors above, then double-click this file again.
        pause
        exit /b 1
    )
    if exist ".venv\Scripts\python.exe" (
        set "PYTHON_BIN=.venv\Scripts\python.exe"
    ) else (
        echo.
        echo [ADGS] .venv still missing after setup. Re-run setup_new_pc.bat.
        pause
        exit /b 1
    )
)

echo.
echo [ADGS] Starting... (type a task to begin, /help for commands)
echo.
"%PYTHON_BIN%" main.py %*
echo.
pause
