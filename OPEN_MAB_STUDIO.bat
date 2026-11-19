@echo off
setlocal
cd /d "%~dp0"
rem Normal app launch. It reuses an already QA-accepted EXE; it does not rebuild.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0AUTO_UPDATE_STUDIO.ps1"
if errorlevel 1 (
  echo.
  echo Normal launch failed. See the error above. No automatic rebuild.
  pause
  exit /b 1
)
exit /b 0
