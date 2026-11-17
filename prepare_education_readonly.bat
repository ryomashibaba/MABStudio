@echo off
setlocal
cd /d "%~dp0"
rem Opt-in: install the reviewed SocketBE package inside Studio only.
rem Never connect to Minecraft, modify worlds, or install packages globally.
where npm.cmd >nul 2>nul
if errorlevel 1 goto :missing_npm
call npm.cmd install --prefix "app\education_runtime" --omit=dev --ignore-scripts --no-audit --no-fund --package-lock
if errorlevel 1 goto :prepare_failed
node -e "const v=require('./app/education_runtime/node_modules/socket-be/package.json').version; if(v!=='2.8.0')process.exit(2)"
if errorlevel 1 goto :prepare_failed
node "app\education_runtime\bridge.mjs" --self-test
if errorlevel 1 goto :prepare_failed
echo Education read-only runtime prepared in Studio-local folder.
exit /b 0
:missing_npm
echo ERROR: npm.cmd not found. Install Node.js 22+ before building.
exit /b 1
:prepare_failed
echo ERROR: Education read-only transport preparation failed.
exit /b 1
