@echo off
setlocal
cd /d "%~dp0"
rem Build tooling remains in this project's private virtual environment.
rem No global Python package upgrades or Minecraft/World writes.
if not exist ".venv-build\Scripts\python.exe" (
  py -3 -m venv .venv-build || goto :error
)
".venv-build\Scripts\python.exe" -m pip install --disable-pip-version-check pyinstaller || goto :error
rem Package the specific SocketBE 2.8.0 transport only for the opt-in read probe.
call prepare_education_readonly.bat || goto :error
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name MABStudio --paths . --add-data "app\engine_bridge.mjs;app" --add-data "app\education_runtime;app\education_runtime" app\main.py || goto :error
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
rem Audit the *copied* private Node and SocketBE; publish only with a verified manifest.
".venv-build\Scripts\python.exe" -m app.release_audit "dist\MABStudio" || goto :error
echo Result: dist\MABStudio\MABStudio.exe
exit /b 0
:error
echo Build failed
if /I "%MAB_STUDIO_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
