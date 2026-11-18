@echo off
setlocal
cd /d "%~dp0"
echo [MAB Studio] One-time model streaming timeout fix: build and verify a NEW isolated EXE.
echo Temporary 3D test windows may open and close during Windows QA.
echo No local AI request or Minecraft world write is performed.
echo After this update, use OPEN_MAB_STUDIO.bat for normal starts.
echo.
set "MAB_STUDIO_NO_PAUSE=1"
call "%~dp0RUN_WINDOWS_NATIVE_QA.bat"
set "QA_RESULT=%ERRORLEVEL%"
if not "%QA_RESULT%"=="0" goto :qa_failed
echo.
echo [MAB Studio] Windows QA passed. Opening Studio with the fixed model transport.
set "MAB_STUDIO_NO_PAUSE="
call "%~dp0OPEN_MAB_STUDIO.bat"
if errorlevel 1 goto :launch_failed
echo [MAB Studio] Future launches: OPEN_MAB_STUDIO.bat (no rebuild).
exit /b 0
:qa_failed
echo [MAB Studio] New EXE QA failed. Prior accepted EXE is unchanged.
echo Exit code: %QA_RESULT%
pause
exit /b %QA_RESULT%
:launch_failed
echo [MAB Studio] Updated QA passed but GUI launch failed. Prior EXE is unchanged.
pause
exit /b 1
