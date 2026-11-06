@echo off
rem ============================================================
rem  ADGS web3 (3rd-gen frontend) launcher
rem  Same family as start_web.bat (v1) / start_web2.bat (v2)
rem
rem  Double-click to run:
rem   [1/3] build web3/dist if missing (npm run build)
rem   [2/3] start Flask backend on 8021 if not listening (bg)
rem   [3/3] open browser http://127.0.0.1:8021/web3/
rem
rem  Notes:
rem   - web3 is a built frontend (React + Vite + TS); dist/ is
rem     served by Flask (web_app.py) under /web3/. v1 (/), v2
rem     (/web2/) and v3 (/web3/) coexist.
rem   - The backend is started in the background. This window is
rem     a read-only log viewer, including when reusing a backend.
rem   - All labels are top-level (never nested in if/else blocks).
rem ============================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "ADGS=%CD%"
set "URL=http://127.0.0.1:8021/web3/"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
set "ADGS_PY=%PY%"

rem ---- [1/3] build web3 static bundle if missing ----------------
if exist "web3\dist\index.html" goto build_skip
echo [1/3] Building web3 (first run or dist missing)...
if exist "C:\Program Files\nodejs\node.exe" set "PATH=C:\Program Files\nodejs;%PATH%"
where node >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] node not found. Install Node.js and retry.
    pause
    exit /b 1
)
pushd web3
set "npm_config_cache=%CD%\.npm-cache"
call npm run build
popd
if not exist "web3\dist\index.html" (
    echo   [ERROR] Build failed: web3\dist\index.html missing.
    pause
    exit /b 1
)
:build_skip
echo [1/3] web3\dist exists, skipping build

rem ---- [2/3] Reuse the backend or start a detached proxy ---------
rem The console below observes shared records; never stop a live runtime here.
call :port_up 8021
if errorlevel 1 goto backend_start
echo   Port 8021 already has a backend running — reusing it.
goto backend_done
:backend_start
echo [2/3] Starting backend 8021 in the background...
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $logDir=Join-Path $env:ADGS '.adgs_runtime'; New-Item -ItemType Directory -Path $logDir -Force | Out-Null; Start-Process -FilePath $env:ADGS_PY -ArgumentList '-u','web_app.py','--no-console-log' -WorkingDirectory $env:ADGS -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'web.out.log') -RedirectStandardError (Join-Path $logDir 'web.err.log')"
if errorlevel 1 goto startfail
set /a i=0
:wait_backend
call :port_up 8021
if not errorlevel 1 goto backend_ready
if %i% GEQ 30 goto startfail
set /a i+=1
timeout /t 1 /nobreak >nul
goto wait_backend
:backend_ready
echo   Backend ready; task runtime survives this log window.
goto backend_done
:backend_done

rem ---- [3/3] open browser ---------------------------------------
echo [3/3] Opening %URL%
start "" "%URL%"
title ADGS live log
echo [logs] Attaching to the running system; closing this window only closes the viewer.
"%PY%" -u tools\adgs_console.py
exit /b 0

:startfail
echo   [ERROR] Backend not ready within 30s. Check manually:
echo          cd "%ADGS%"
echo          .\.venv\Scripts\python.exe -u web_app.py
pause
exit /b 1

rem ---- subroutine: is the port LISTENING? (0=yes, 1=no) ---------
:port_up
netstat -an | findstr /C:":%1 " | findstr /C:"LISTENING" >nul 2>&1
exit /b %errorlevel%
