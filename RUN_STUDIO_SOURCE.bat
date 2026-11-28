@echo off
setlocal
cd /d "%~dp0"
rem Run the CURRENT Studio source instead of any older packaged EXE.
rem Does not rebuild, install packages, or write to a Minecraft world.
set "MAB_STUDIO_TMP=%~dp0work\runtime-tmp"
if not exist "%MAB_STUDIO_TMP%" mkdir "%MAB_STUDIO_TMP%"
set "TEMP=%MAB_STUDIO_TMP%"
set "TMP=%MAB_STUDIO_TMP%"
rem Prefer the existing, one_click-verified isolated VTK runtime when available.
if not exist "%~dp0.venv-native\Scripts\python.exe" goto :source_fallback
"%~dp0.venv-native\Scripts\python.exe" -c "from app.one_click import _dependency_probe_script; exec(_dependency_probe_script())" >nul 2>nul
if errorlevel 1 goto :source_fallback
set "MAB_STUDIO_NATIVE=1"
set "MAB_STUDIO_ONE_CLICK_FALLBACK=0"
"%~dp0.venv-native\Scripts\python.exe" -m app.main
goto :done
:source_fallback
set "MAB_STUDIO_NATIVE=0"
set "MAB_STUDIO_ONE_CLICK_FALLBACK=1"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; assert sys.version_info[:2] == (3,13) and sys.maxsize > 2**32" >nul 2>nul
  if not errorlevel 1 goto :py313
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; assert sys.version_info[:2] == (3,13) and sys.maxsize > 2**32" >nul 2>nul
  if not errorlevel 1 goto :python313
)
echo Source preview requires installed 64-bit Python 3.13.
if /I "%MAB_STUDIO_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
:py313
py -3 -m app.main
goto :done
:python313
python -m app.main
:done
if errorlevel 1 (
  echo Source MAB Studio encountered a startup error.
  if /I "%MAB_STUDIO_NO_PAUSE%"=="1" exit /b 1
  pause
  exit /b 1
)
exit /b 0
