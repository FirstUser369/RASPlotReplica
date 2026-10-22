@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RAS-Plot

if not exist "%~dp0runtime\python\python.exe" (
  echo ERROR: The bundled RAS-Plot Python runtime is missing.
  echo Copy the complete RAS-Plot folder and try again.
  pause
  exit /b 1
)

"%~dp0runtime\python\python.exe" -B -I "%~dp0app\launcher.py"
set "RESULT=%ERRORLEVEL%"
endlocal & exit /b %RESULT%
