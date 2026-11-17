# One-click, isolated native Windows build + packaged EXE SOURCE/GUI test.
# The only model endpoint exercised is a deliberately marked loopback TEST DOUBLE.
# It does NOT start Ollama/LM Studio, run genuine inference, or touch Minecraft.
$ErrorActionPreference = 'Stop'
$source = $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$studioDataRoot = Join-Path $env:LOCALAPPDATA 'MABStudio'
$results = Join-Path $studioDataRoot ('diagnostics\windows-native-' + $stamp)
$work = Join-Path $studioDataRoot ('qa-work-' + $stamp)
New-Item -ItemType Directory -Force -Path $results,$work | Out-Null
$summaryFile = Join-Path $results 'WINDOWS_NATIVE_QA.json'
$summary = [ordered]@{
    schema = 'mab-studio-windows-native-acceptance-v1'
    createdAt = (Get-Date).ToUniversalTime().ToString('o')
    outcome = 'FAIL'
    stage = 'COPY_ISOLATED_SOURCE'
    genuineModelInference = 'NOT_RUN'
    minecraftEducation = 'NOT_CONNECTED'
    worldWrites = 0
    sourceCommit = 'b0295c2f5012877f57ae218ae791634794de7b52'
    sourcePinnedHash = '537aa9e4a2b4e027f16179ef7a306b604ca4d850713d6de23940667ed2348e16'
    releaseLicenseClearance = 'NOT_CERTIFIED'
    windowsExeSmoke = 'NOT_RUN'
    testDoubleGuiAcceptance = 'NOT_RUN'
}
$fixture = $null
try {
    # Never build over the original checked-out directory or an earlier dist.
    foreach ($item in @('app','tests','build_windows.bat','prepare_education_readonly.bat')) {
        Copy-Item -LiteralPath (Join-Path $source $item) -Destination $work -Recurse -Force
    }
    Set-Location -LiteralPath $work
    $summary.stage = 'PINNED_B143_DOWNLOAD'
    $engine = Join-Path $work 'verified-engine'
    $js = Join-Path $engine 'source\ai\b143-building-dsl.mjs'
    New-Item -ItemType Directory -Force (Split-Path $js -Parent) | Out-Null
    $url = 'https://raw.githubusercontent.com/ryomashibaba/MinecraftAIBuilder/' + $summary.sourceCommit + '/source/ai/b143-building-dsl.mjs'
    Invoke-WebRequest -Uri $url -OutFile $js
    $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $js).Hash.ToLowerInvariant()
    if ($digest -cne $summary.sourcePinnedHash) { throw 'PINNED_B143_HASH_MISMATCH' }

    $summary.stage = 'WINDOWS_BUILD_IN_SANDBOX'
    $env:MAB_STUDIO_NO_PAUSE = '1'
    & (Join-Path $work 'build_windows.bat')
    if ($LASTEXITCODE -ne 0) { throw 'ISOLATED_WINDOWS_BUILD_FAILED' }
    $exe = Join-Path $work 'dist\MABStudio\MABStudio.exe'
    if (!(Test-Path -LiteralPath $exe)) { throw 'WINDOWS_EXE_MISSING' }
    $summary.stage = 'REAL_WINDOWS_EXE_SMOKE'
    $smoke = Join-Path $results 'EXE_SMOKE.json'
    $smokeArgs = @('--smoke','--smoke-engine',('"'+$engine+'"'),'--smoke-report',('"'+$smoke+'"'))
    $smokeProcess = Start-Process -FilePath $exe -ArgumentList $smokeArgs -WorkingDirectory $work -Wait -PassThru
    if ($smokeProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $smoke)) { throw 'REAL_WINDOWS_EXE_SMOKE_FAILED' }
    $receipt = Get-Content -Raw -LiteralPath $smoke -Encoding UTF8 | ConvertFrom-Json
    if ($receipt.result -ne 'PASS' -or $receipt.sourceVoxels -ne 917 -or $receipt.worldWrites -ne 0) {
        throw 'SMOKE_RECEIPT_INCONSISTENT'
    }
    $summary.windowsExeSmoke = 'PASS_PINNED_B143_AND_TK'

    $summary.stage = 'ISOLATED_TEST_DOUBLE'
    $ready = Join-Path $work 'fixture-ready.json'
    $python = Join-Path $work '.venv-build\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'ISOLATED_PYTHON_MISSING' }
    # Port 0: OS selects a free loopback port. Does not occupy Ollama's 11434.
    $fixtureArguments = @('-m','tests.windows_acceptance_fixture','--port','0','--ready-file', ('"'+$ready+'"'))
    $fixture = Start-Process -FilePath $python -ArgumentList $fixtureArguments -WorkingDirectory $work -PassThru -WindowStyle Hidden
    $isReady = $false
    foreach ($attempt in 1..60) {
        if (Test-Path -LiteralPath $ready) { $isReady = $true; break }
        if ($fixture.HasExited) { break }
        Start-Sleep -Milliseconds 250
    }
    if (!$isReady) { throw 'ISOLATED_LOOPBACK_FIXTURE_NOT_READY' }
    $port = (Get-Content -Raw -LiteralPath $ready -Encoding UTF8 | ConvertFrom-Json).port
    if ($port -lt 1 -or $port -gt 65535) { throw 'ISOLATED_LOOPBACK_PORT_INVALID' }
    $report = Join-Path $results 'EXE_TEST_DOUBLE_ACCEPTANCE.json'
    $acceptArgs = @('--acceptance','--provider','Ollama','--model','CI_TEST_DOUBLE',
                    '--test-double','--fixture-port',[string]$port,'--engine',('"'+$engine+'"'),
                    '--report',('"'+$report+'"'))
    $acceptProcess = Start-Process -FilePath $exe -ArgumentList $acceptArgs -WorkingDirectory $work -Wait -PassThru
    if ($acceptProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $report)) { throw 'EXE_TEST_DOUBLE_ACCEPTANCE_FAILED' }
    $acceptance = Get-Content -Raw -LiteralPath $report -Encoding UTF8 | ConvertFrom-Json
    if ($acceptance.outcome -ne 'PASS_TEST_DOUBLE_SOURCE_AND_GUI_ONLY' -or
        !$acceptance.testDoubleUsed -or !$acceptance.testDoubleRequestAttempted -or
        $acceptance.actualProviderRequestAttempted -or !$acceptance.sourceValid -or
        !$acceptance.guiRendered -or $acceptance.source.voxelCount -ne 917 -or
        $acceptance.worldWrites -ne 0) { throw 'EXE_TEST_DOUBLE_RESULT_INCONSISTENT' }
    $summary.testDoubleGuiAcceptance = 'PASS_FAKE_MODEL_ONLY'

    $summary.stage = 'ARCHIVE_VERIFIED_EXE'
    # Preserve hidden npm files and original onedir layout in the ZIP.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $release = Join-Path $results 'MABStudio-Windows-Safe-Preview.zip'
    [System.IO.Compression.ZipFile]::CreateFromDirectory((Join-Path $work 'dist'),$release)
    $summary.previewZip = $release
    $summary.previewZipSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $release).Hash.ToLowerInvariant()
    $summary.outcome = 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY'
    $summary.stage = 'COMPLETE'
} catch {
    $summary.outcome = 'FAIL'
    $summary.error = [string]$_.Exception.Message
} finally {
    if ($null -ne $fixture -and !$fixture.HasExited) {
        Stop-Process -Id $fixture.Id -Force -ErrorAction SilentlyContinue
    }
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryFile -Encoding UTF8
    Write-Host ('MAB Studio Windows QA: ' + $summary.outcome)
    Write-Host ('Report: ' + $summaryFile)
    if ($summary.error) { Write-Host ('Error: ' + $summary.error) }
    Remove-Item Env:MAB_STUDIO_NO_PAUSE -ErrorAction SilentlyContinue
    # Working files are isolated and disposable; do not alter original Studio.
    Set-Location -LiteralPath $source
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
if ($summary.outcome -eq 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY') { exit 0 }
exit 1
