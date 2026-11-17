@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_WINDOWS_ACCEPTANCE.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
echo Windows acceptance finished, exit code %EXITCODE%.
pause
exit /b %EXITCODE%
