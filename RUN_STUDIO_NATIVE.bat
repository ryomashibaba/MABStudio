@echo off
setlocal
cd /d "%~dp0"
rem Native GPU preview - entirely inside Studio. Never launches a browser.
rem Separate, private venv: no global pip updates or Minecraft writes.
if not exist ".venv-native\Scripts\python.exe" (
  py -3.13 -m venv .venv-native || goto :error
)
".venv-native\Scripts\python.exe" -c "import vtkmodules.vtkRenderingOpenGL2; from PIL import ImageTk" >nul 2>nul
if errorlevel 1 (
  echo First run: installing native 3D runtime in Studio's private environment...
  ".venv-native\Scripts\python.exe" -m pip install --disable-pip-version-check "vtk==9.6.2" "Pillow>=10,<13" || goto :error
)
set MAB_STUDIO_NATIVE=1
".venv-native\Scripts\python.exe" -m app.main
if errorlevel 1 goto :error
exit /b 0
:error
echo Native 3D startup failed. Existing project, SOURCE and Minecraft world were not modified.
if /I "%MAB_STUDIO_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
