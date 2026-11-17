@echo off
setlocal
cd /d "%~dp0"
rem One-click: no browser, no admin, private native 3D setup and QA.
rem No local AI inference and no Minecraft world access in the launcher.
where py >nul 2>nul
if not errorlevel 1 (
  py -3.13 -c "import sys; assert sys.maxsize > 2**32" >nul 2>nul
  if not errorlevel 1 goto :py313
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; assert sys.version_info[:2] == (3,13) and sys.maxsize > 2**32" >nul 2>nul
  if not errorlevel 1 goto :python313
)
echo This Studio requires installed 64-bit Python 3.13 on Windows 11.
echo No packages or Minecraft/AI data were changed.
if /I "%MAB_STUDIO_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
:py313
py -3.13 -m app.one_click
exit /b %errorlevel%
:python313
python -m app.one_click
exit /b %errorlevel%
