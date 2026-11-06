@echo off
rem ============================================
rem  ADGS setup launcher (double-click to run)
rem  Runs setup_new_pc.ps1 with execution policy
rem  bypass so it works on any fresh Windows box.
rem ============================================
title ADGS Setup
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_new_pc.ps1" %*
echo.
echo Exit code: %ERRORLEVEL%
pause
