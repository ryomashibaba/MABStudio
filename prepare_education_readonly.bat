@echo off
setlocal
cd /d "%~dp0"
rem Explicit opt-in: install only the pinned SocketBE library under this app folder.
rem No Minecraft interaction, no world write, no global npm install.
where npm.cmd >nul 2>nul || (echo Node.js/npm が見つかりません & exit /b 1)
call npm.cmd install --prefix "app\education_runtime" --omit=dev --ignore-scripts --no-audit --no-fund --package-lock || exit /b 1
node -e "const v=require('./app/education_runtime/node_modules/socket-be/package.json').version; if(v!=='2.8.0')process.exit(2)" || exit /b 1
node "app\education_runtime\bridge.mjs" --self-test || exit /b 1
echo Education読取コンポーネントをアプリ専用フォルダへ導入しました。
exit /b 0
