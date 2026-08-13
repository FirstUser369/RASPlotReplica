@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8765"
set "URL=http://%HOST%:%PORT%/"

rem Prefer a portable python next to this folder, then common install locations
set "PY="
if exist "%~dp0vendor\python\python.exe" set "PY=%~dp0vendor\python\python.exe"
if not defined PY if exist "%~dp0..\python\python.exe" set "PY=%~dp0..\python\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "C:\Users\kopczyknj\Dev\python\python.exe" set "PY=C:\Users\kopczyknj\Dev\python\python.exe"
if not defined PY where python >nul 2>&1 && for /f "delims=" %%I in ('where python') do (
  set "PY=%%I"
  goto :have_py
)
:have_py
if not defined PY (
  echo ERROR: No Python interpreter found.
  echo Place a portable Python under RASPlotReplica\vendor\python\ or install Python 3.12+.
  pause
  exit /b 1
)

echo Using Python: %PY%
"%PY%" -c "import fastapi, h5py, matplotlib" 2>nul
if errorlevel 1 (
  echo Installing backend requirements into this Python...
  "%PY%" -m pip install -r "%~dp0backend\requirements.txt"
  if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
  )
)

if not exist "%~dp0backend\app\static\index.html" (
  echo WARNING: frontend build missing at backend\app\static\
  echo Run "npm install" and "npm run build" in frontend\ if you need the UI.
)

echo Starting RAS-Plot Replica on %URL%
start "" "%URL%"
cd /d "%~dp0backend"
"%PY%" -m uvicorn app.main:app --host %HOST% --port %PORT%
endlocal
