@echo off
setlocal
cd /d "%~dp0"
rem Build tooling remains in this project's private virtual environment.
rem No global Python package upgrades or Minecraft/World writes.
if not exist ".venv-build\Scripts\python.exe" (
  py -3 -m venv .venv-build || goto :error
)
".venv-build\Scripts\python.exe" -m pip install --disable-pip-version-check pyinstaller || goto :error
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name MABStudio --paths . --add-data "app\engine_bridge.mjs;app" app\main.py || goto :error
where node >nul 2>nul || (echo Node.js is needed for packaged edition & exit /b 1)
for /f "delims=" %%i in ('where node') do (
  copy /Y "%%i" "dist\MABStudio\node.exe" >nul
  if not exist "%%~dpiLICENSE" (
    echo Node.js license missing. Distribution cannot proceed.
    goto :error
  )
  copy /Y "%%~dpiLICENSE" "dist\MABStudio\NODE_LICENSE.txt" >nul || goto :error
  goto :done
)
:done
echo Result: dist\MABStudio\MABStudio.exe
exit /b 0
:error
echo Build failed
pause
exit /b 1
