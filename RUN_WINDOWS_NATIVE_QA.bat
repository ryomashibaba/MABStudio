@echo off
setlocal
cd /d "%~dp0"
rem A single opt-in Windows acceptance run. Builds in a NEW isolated workspace.
rem No real model inference, Minecraft connection or world modifications.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_WINDOWS_NATIVE_QA.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
echo.
echo MAB Studio Windows native QA exit code: %EXITCODE%
if /I not "%MAB_STUDIO_NO_PAUSE%"=="1" pause
exit /b %EXITCODE%
