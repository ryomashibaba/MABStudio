@echo off
setlocal
cd /d "%~dp0"
rem Run the CURRENT Studio source instead of any older packaged EXE.
rem Does not rebuild, install packages, or write to a Minecraft world.
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m app.main
  goto :done
)
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python -m app.main
  goto :done
)
echo Python 3 is required to launch this source preview.
pause
exit /b 1
:done
if errorlevel 1 (
  echo Source MAB Studio encountered a startup error.
  pause
  exit /b 1
)
exit /b 0
