@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m app.main
  if errorlevel 1 pause
) else (
  where python >nul 2>nul
  if %ERRORLEVEL% NEQ 0 (
    echo Python 3 not found. Install Python or use the Windows packaged version.
    pause
    exit /b 1
  )
  python -m app.main
  if errorlevel 1 pause
)
