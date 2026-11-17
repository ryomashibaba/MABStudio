@echo off
setlocal
cd /d "%~dp0"
rem User-first: the packaged Windows app does NOT need Python or an external Node.
rem Keep the full dist\MABStudio folder intact; never fall back to a different
rem runtime silently when a packaged EXE is present but incomplete.
if exist "dist\MABStudio\MABStudio.exe" (
  if not exist "dist\MABStudio\node.exe" goto :broken
  if not exist "dist\MABStudio\NODE_LICENSE.txt" goto :broken
  start "" "dist\MABStudio\MABStudio.exe"
  exit /b %ERRORLEVEL%
)
rem Development source checkout: Python and Node.js 22+ required.
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m app.main
  if errorlevel 1 goto :source_failed
  exit /b 0
)
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python -m app.main
  if errorlevel 1 goto :source_failed
  exit /b 0
)
echo No packaged EXE or Python found.
echo For regular users, download a verified Windows dist\MABStudio build.
echo This ZIP contains source code, not a verified prebuilt EXE.
pause
exit /b 1
:broken
echo Packaged MAB Studio is incomplete (private Node.js or license missing).
echo Unzip the entire dist\MABStudio folder again. Do not copy MABStudio.exe alone.
pause
exit /b 1

:source_failed
echo Studio source startup failed. Verify Python 3.10+ and Node.js 22+.
pause
exit /b 1
