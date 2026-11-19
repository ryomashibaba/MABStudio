@echo off
setlocal
cd /d "%~dp0"
rem Explicit opt-in: building a native VTK Windows EXE is separate from daily source use.
rem No global Python upgrades, local LLM activity or Minecraft world modification.
if not exist ".venv-native\Scripts\python.exe" (
  rem Prefer an installed Python 3.13 executable when the Windows py launcher
  rem has no registered interpreters (common in managed Windows sessions).
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,13) else 1)" >nul 2>nul
  if errorlevel 1 (
    py -3.13 -m venv .venv-native || goto :error
  ) else (
    python -m venv .venv-native || goto :error
  )
)
".venv-native\Scripts\python.exe" -m pip install --disable-pip-version-check "vtk==9.6.2" "Pillow>=10,<13" "pyinstaller" || goto :error
call prepare_education_readonly.bat || goto :error
".venv-native\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name MABStudio --distpath dist-native --paths . --hidden-import vtkmodules.vtkRenderingOpenGL2 --add-data "app\engine_bridge.mjs;app" --add-data "app\gpu_viewer_assets;app\gpu_viewer_assets" --add-data "app\education_runtime;app\education_runtime" app\main.py || goto :error
".venv-native\Scripts\python.exe" -m app.node_packaging "dist-native\MABStudio" || goto :error
".venv-native\Scripts\python.exe" -m app.release_audit "dist-native\MABStudio" || goto :error
".venv-native\Scripts\python.exe" -m app.native_license "dist-native\MABStudio" || goto :error
echo Native EXE candidate: dist-native\MABStudio\MABStudio.exe
echo Must be accepted on Windows before claiming native GPU compatibility.
exit /b 0
:error
echo Native build FAILED. Existing builds, SOURCE and Minecraft worlds are unchanged.
if /I "%MAB_STUDIO_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
