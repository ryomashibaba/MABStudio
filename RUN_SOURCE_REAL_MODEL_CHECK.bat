@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_SOURCE_REAL_MODEL_CHECK.ps1"
set "RC=%ERRORLEVEL%"
echo.
echo MAB Studio source real-model check exit code: %RC%
pause
exit /b %RC%
