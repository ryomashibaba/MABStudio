# One user-triggered acceptance for Windows native EXE + an explicitly chosen local model.
# Does not install models, change Minecraft worlds, or change system-wide settings.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'MAB Studio Windows 実機受入テスト（モデルへの真正リクエストあり）'
Write-Host '必要: Python 3.10+ / Node.js 22+ / 起動済みOllamaまたはLM Studio。'
Write-Host 'テストは設計と3D描画だけを行い、Minecraftには接続しません。'
$exe = Join-Path $PSScriptRoot 'dist\MABStudio\MABStudio.exe'
if (!(Test-Path -LiteralPath $exe)) {
    $build = Read-Host 'EXEがありません。専用仮想環境へのPyInstaller導入・ビルドを実行しますか？ (YESのみ実行)'
    if ($build -cne 'YES') { Write-Host '中止しました。'; exit 2 }
    & (Join-Path $PSScriptRoot 'build_windows.bat')
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $exe)) {
        throw 'WINDOWS_EXE_BUILD_FAILED'
    }
}
Write-Host 'ローカルAIをユーザー自身で先に起動し、使うモデルをロードしてください。'
$choice = Read-Host '利用するプロバイダー (1=Ollama, 2=LM Studio)'
if ($choice -eq '1') { $provider = 'Ollama' }
elseif ($choice -eq '2') { $provider = 'LM Studio' }
else { Write-Host '不正な選択です。'; exit 2 }
$model = (Read-Host 'ロード済みのモデルID (省略不可)').Trim()
if (!$model) { Write-Host 'モデルIDが空です。'; exit 2 }
$prompt = Read-Host '受入用の日本語指示（空欄で天文観測所の既定例）'
if (!$prompt) { $prompt = '海辺の小さな天文観測所。地上階の入口、窓、屋上の丸い展望ドームを備えた独創的な建物を設計して。' }
$consent = Read-Host 'この指示でローカルAI推論を1回実行します。高負荷の可能性があります。続行しますか？ (YESのみ実行)'
if ($consent -cne 'YES') { Write-Host '推論は実行していません。'; exit 2 }
$folder = Join-Path $env:LOCALAPPDATA 'MABStudio\diagnostics\acceptance'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$report = Join-Path $folder ('windows-acceptance-' + $stamp + '.json')
& $exe '--acceptance' '--provider' $provider '--model' $model '--prompt' $prompt '--report' $report
$exitCode = $LASTEXITCODE
if (!(Test-Path -LiteralPath $report)) { throw 'ACCEPTANCE_REPORT_MISSING' }
$result = Get-Content -Raw -LiteralPath $report -Encoding UTF8 | ConvertFrom-Json
Write-Host ('結果: ' + $result.outcome + ' / 停止段階: ' + $result.stage)
if ($result.error) { Write-Host ('原因: ' + $result.error) }
if ($result.source) { Write-Host ('SOURCEセル数: ' + $result.source.voxelCount) }
Write-Host ('保存した受入結果: ' + $report)
Write-Host 'この試験はMinecraft施工や建築の美しさの認証ではありません。'
exit $exitCode
