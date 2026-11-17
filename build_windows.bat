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
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name MABStudio --paths . --add-data "app\engine_bridge.mjs;app" --add-data "app\gpu_viewer_assets;app\gpu_viewer_assets" --add-data "app\education_runtime;app\education_runtime" app\main.py || goto :error
rem Node.js Windows installers may omit a sibling LICENSE file.  Fetch the
rem official matching-version notice when needed; fail closed on error.
".venv-build\Scripts\python.exe" -m app.node_packaging "dist\MABStudio" || goto :error
rem Audit the *copied* private Node and SocketBE; publish only with a verified manifest.
".venv-build\Scripts\python.exe" -m app.release_audit "dist\MABStudio" || goto :error
echo Result: dist\MABStudio\MABStudio.exe
exit /b 0
:error
echo Build failed
if /I "%MAB_STUDIO_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
