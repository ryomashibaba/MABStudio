@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0AUTO_UPDATE_STUDIO.ps1" -ChooseFeed
if errorlevel 1 pause
