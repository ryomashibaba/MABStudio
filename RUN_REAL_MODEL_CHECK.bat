@echo off
setlocal
cd /d "%~dp0"
rem One genuine local-model check with an EXISTING validated EXE.
rem Does not build, install packages, load models, or touch Minecraft.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_REAL_MODEL_CHECK.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
echo MAB Studio real-model check exit code: %EXITCODE%
pause
exit /b %EXITCODE%
