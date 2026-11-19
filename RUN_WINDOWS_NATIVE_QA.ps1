# One-click, isolated native Windows build + packaged EXE SOURCE/GUI test.
# The only model endpoint exercised is a deliberately marked loopback TEST DOUBLE.
# It does NOT start Ollama/LM Studio, run genuine inference, or touch Minecraft.
param(
    [ValidatePattern('^[0-9a-f]{32}$')][string]$SavedRevisionId = ''
)
. (Join-Path $PSScriptRoot 'GET_STUDIO_SOURCE_FINGERPRINT.ps1')
$ErrorActionPreference = 'Stop'
function Get-NativeSha256 {
    param([Parameter(Mandatory=$true)][string]$Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try { return [System.BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
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
    nativeRenderSelfCheck = 'NOT_RUN'
    packagedNativeRenderSelfCheck = 'NOT_RUN'
    windowsExeSmoke = 'NOT_RUN'
    portableZipNativeSelfCheck = 'NOT_RUN'
    portableZipSourceGui = 'NOT_RUN'
    savedRevisionNative3D = 'NOT_REQUESTED'
    testDoubleGuiAcceptance = 'NOT_RUN'
    savedRevisionRendererDiagnostics = 'NOT_REQUESTED'
    savedRevisionInteractionTimingsMs = 'NOT_REQUESTED'
    savedRevisionIdleFrames = 'NOT_REQUESTED'
    savedRevisionWorkingSetMb = 'NOT_REQUESTED'
    hardwareGpuIndependentlyVerified = $false
    thirdPartyLicenseFilesIncluded = $false
    previewZipBytes = 0
    sourceFingerprintSha256 = ''
}
$fixture = $null
try {
    $summary.stage = 'VALIDATE_WINDOWS_BATCH_FORMAT'
    $summary.sourceFingerprintSha256 = Get-StudioSourceFingerprint -Root $source
    # The original release accidentally shipped LF-only BAT files. Fail before installing
    # dependencies rather than producing misleading cmd.exe parser errors.
    foreach ($batch in @('RUN_WINDOWS_NATIVE_QA.bat','build_windows_native.bat','START_MAB_STUDIO.bat','prepare_education_readonly.bat')) {
        $data = [System.IO.File]::ReadAllBytes((Join-Path $source $batch))
        if ($data.Length -lt 10) { throw ('INVALID_WINDOWS_BATCH_FILE: ' + $batch) }
        for ($i = 0; $i -lt $data.Length; $i++) {
            if ($data[$i] -gt 127) { throw ('NON_ASCII_WINDOWS_BATCH_FILE: ' + $batch) }
            if ($data[$i] -eq 10 -and ($i -eq 0 -or $data[$i-1] -ne 13)) {
                throw ('LF_ONLY_WINDOWS_BATCH_FILE: ' + $batch)
            }
        }
    }
    # Never build over the original checked-out directory or an earlier dist.
    foreach ($item in @('app','tests','build_windows_native.bat','prepare_education_readonly.bat')) {
        Copy-Item -LiteralPath (Join-Path $source $item) -Destination $work -Recurse -Force
    }
    if ((Get-StudioSourceFingerprint -Root $work) -cne $summary.sourceFingerprintSha256) {
        throw 'ISOLATED_STUDIO_SOURCE_FINGERPRINT_MISMATCH'
    }
    Set-Location -LiteralPath $work
    $summary.stage = 'PINNED_B143_SOURCE'
    $engine = Join-Path $work 'verified-engine'
    $js = Join-Path $engine 'source\ai\b143-building-dsl.mjs'
    New-Item -ItemType Directory -Force (Split-Path $js -Parent) | Out-Null
    # Prefer the user's already-installed, pinned Studio engine when it exists.
    # Do not mutate that engine. A mismatching local file fails closed rather than
    # silently falling back to network or a different MAB commit.
    $localEngine = Join-Path (Join-Path (Join-Path $studioDataRoot 'engines') $summary.sourceCommit) 'source\ai\b143-building-dsl.mjs'
    if (Test-Path -LiteralPath $localEngine -PathType Leaf) {
        if ((Get-NativeSha256 -Path $localEngine) -cne $summary.sourcePinnedHash) {
            throw 'LOCAL_PINNED_B143_HASH_MISMATCH'
        }
        Copy-Item -LiteralPath $localEngine -Destination $js -Force
        $summary.b143Source = 'LOCAL_SHA256_VERIFIED_COPY'
    } else {
        $summary.stage = 'PINNED_B143_DOWNLOAD'
        $url = 'https://raw.githubusercontent.com/ryomashibaba/MinecraftAIBuilder/' + $summary.sourceCommit + '/source/ai/b143-building-dsl.mjs'
        Invoke-WebRequest -Uri $url -OutFile $js
        $summary.b143Source = 'NETWORK_SHA256_VERIFIED_DOWNLOAD'
    }
    $digest = Get-NativeSha256 -Path $js
    if ($digest -cne $summary.sourcePinnedHash) { throw 'PINNED_B143_HASH_MISMATCH' }

    $summary.stage = 'WINDOWS_BUILD_IN_SANDBOX'
    $env:MAB_STUDIO_NO_PAUSE = '1'
    & (Join-Path $work 'build_windows_native.bat')
    if ($LASTEXITCODE -ne 0) { throw 'ISOLATED_WINDOWS_BUILD_FAILED' }
    $summary.stage = 'WINDOWS_NATIVE_SYNTHETIC_RENDER_QA'
    $python = Join-Path $work '.venv-native\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'PRIVATE_NATIVE_PYTHON_MISSING' }
    $nativeReceiptPath = Join-Path $results 'NATIVE_RENDER_SMOKE.json'
    & $python -m app.native_selfcheck --report $nativeReceiptPath
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $nativeReceiptPath)) { throw 'NATIVE_RENDER_QA_FAILED' }
    $nativeReceipt = Get-Content -Raw -LiteralPath $nativeReceiptPath -Encoding UTF8 | ConvertFrom-Json
    if ($nativeReceipt.result -ne 'PASS_NATIVE_READONLY_SELF_CHECK' -or
        !$nativeReceipt.paletteChangePixelsVerified -or $nativeReceipt.worldWrites -ne 0) { throw 'NATIVE_RENDER_QA_INCONSISTENT' }
    $summary.nativeRenderSelfCheck = 'PASS_SYNTHETIC_IN_MEMORY'
    $exe = Join-Path $work 'dist-native\MABStudio\MABStudio.exe'
    if (!(Test-Path -LiteralPath $exe)) { throw 'WINDOWS_EXE_MISSING' }
    $summary.stage = 'PACKAGED_NATIVE_3D_RENDER_QA'
    $packedNative = Join-Path $results 'EXE_NATIVE_3D_RENDER.json'
    $packedNativeArgs = @('--native-selfcheck','--extended','--report',('"'+$packedNative+'"'))
    $packedNativeProcess = Start-Process -FilePath $exe -ArgumentList $packedNativeArgs -WorkingDirectory $work -PassThru
    if (!$packedNativeProcess.WaitForExit(120000)) {
        Stop-Process -Id $packedNativeProcess.Id -Force -ErrorAction SilentlyContinue
        throw 'PACKAGED_NATIVE_3D_RENDER_TIMEOUT'
    }
    if ($packedNativeProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $packedNative)) {
        throw 'PACKAGED_NATIVE_3D_RENDER_FAILED'
    }
    $packedCheck = Get-Content -Raw -LiteralPath $packedNative -Encoding UTF8 | ConvertFrom-Json
    if ($packedCheck.result -ne 'PASS_NATIVE_READONLY_SELF_CHECK' -or
        !$packedCheck.packagedRuntimeVerified -or $packedCheck.runtimeMode -ne 'PACKAGED_ONEDIR' -or
        $packedCheck.cells -ne 64 -or !$packedCheck.paletteChangePixelsVerified -or
        $packedCheck.extendedScene.cells -ne 3128 -or
        $packedCheck.extendedScene.paletteEntries -ne 11 -or
        !$packedCheck.extendedScene.pixelChangeVerified -or
        !$packedCheck.extendedScene.cameraPixelChangeVerified -or
        !$packedCheck.extendedScene.dragPreviewVerified -or
        !$packedCheck.extendedScene.fullResolutionRestored -or
        $packedCheck.extendedScene.renderTimings.materialBoundaryEdges -lt 1 -or
        $packedCheck.renderTimings.samples -lt 10 -or
        $packedCheck.worldContacts -ne 0 -or $packedCheck.worldWrites -ne 0) {
        throw 'PACKAGED_NATIVE_3D_EVIDENCE_INVALID'
    }
    $summary.packagedNativeRenderSelfCheck = 'PASS_EXE_SYNTHETIC_3128_CELLS'
    $summary.stage = 'REAL_WINDOWS_EXE_SMOKE'
    $smoke = Join-Path $results 'EXE_SMOKE.json'
    $smokeArgs = @('--smoke','--smoke-engine',('"'+$engine+'"'),'--smoke-report',('"'+$smoke+'"'))
    $smokeProcess = Start-Process -FilePath $exe -ArgumentList $smokeArgs -WorkingDirectory $work -PassThru
    if (!$smokeProcess.WaitForExit(120000)) {
        Stop-Process -Id $smokeProcess.Id -Force -ErrorAction SilentlyContinue
        throw 'PACKAGED_GUI_SMOKE_TIMEOUT'
    }
    if ($smokeProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $smoke)) { throw 'REAL_WINDOWS_EXE_SMOKE_FAILED' }
    $receipt = Get-Content -Raw -LiteralPath $smoke -Encoding UTF8 | ConvertFrom-Json
    if ($receipt.result -ne 'PASS' -or $receipt.sourceVoxels -ne 917 -or $receipt.worldWrites -ne 0 -or
        $receipt.guiRenderer -ne 'VTK_NATIVE_EMBEDDED' -or !$receipt.nativeScenePixelsVerified -or
        $receipt.nativeCells -ne 917) {
        throw 'SMOKE_RECEIPT_INCONSISTENT'
    }
    $summary.windowsExeSmoke = 'PASS_PINNED_B143_AND_TK'

    $summary.stage = 'ISOLATED_TEST_DOUBLE'
    $ready = Join-Path $work 'fixture-ready.json'
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
    $acceptProcess = Start-Process -FilePath $exe -ArgumentList $acceptArgs -WorkingDirectory $work -PassThru
    if (!$acceptProcess.WaitForExit(120000)) {
        Stop-Process -Id $acceptProcess.Id -Force -ErrorAction SilentlyContinue
        throw 'PACKAGED_ACCEPTANCE_TIMEOUT'
    }
    if ($acceptProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $report)) { throw 'EXE_TEST_DOUBLE_ACCEPTANCE_FAILED' }
    $acceptance = Get-Content -Raw -LiteralPath $report -Encoding UTF8 | ConvertFrom-Json
    if ($acceptance.outcome -ne 'PASS_TEST_DOUBLE_SOURCE_AND_GUI_ONLY' -or
        !$acceptance.testDoubleUsed -or !$acceptance.testDoubleRequestAttempted -or
        $acceptance.actualProviderRequestAttempted -or !$acceptance.sourceValid -or
        !$acceptance.guiRendered -or $acceptance.guiRenderer -cne 'VTK_NATIVE_EMBEDDED' -or
        !$acceptance.nativeScenePixelsVerified -or $acceptance.nativeCells -ne 917 -or
        $acceptance.source.voxelCount -ne 917 -or
        $acceptance.worldWrites -ne 0) { throw 'EXE_TEST_DOUBLE_RESULT_INCONSISTENT' }
    $summary.testDoubleGuiAcceptance = 'PASS_FAKE_MODEL_ONLY'

    $summary.stage = 'ARCHIVE_VERIFIED_EXE'
    # Preserve hidden npm files and original onedir layout in the ZIP.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $release = Join-Path $results 'MABStudio-Windows-Native-Preview.zip'
    [System.IO.Compression.ZipFile]::CreateFromDirectory((Join-Path $work 'dist-native'),$release)
    $summary.previewZip = $release
    $summary.previewZipBytes = (Get-Item -LiteralPath $release).Length
    $summary.previewZipSha256 = Get-NativeSha256 -Path $release
    $summary.thirdPartyLicenseFilesIncluded = (Get-ChildItem -LiteralPath (Join-Path $work 'dist-native\MABStudio\THIRD_PARTY_LICENSES') -Recurse -File | Measure-Object).Count -ge 1

    # Test the EXACT archive to be delivered, extracted outside the build tree.
    # Building and testing dist-native alone does not prove the portable ZIP.
    $summary.stage = 'REEXTRACT_PORTABLE_RELEASE_ZIP'
    $portable = Join-Path $results 'portable-extracted-qa'
    New-Item -ItemType Directory -Path $portable | Out-Null
    [System.IO.Compression.ZipFile]::ExtractToDirectory($release, $portable)
    $portableExe = Join-Path $portable 'MABStudio\MABStudio.exe'
    if (!(Test-Path -LiteralPath $portableExe)) { throw 'PORTABLE_RELEASE_EXE_MISSING' }
    $sourceExeHash = Get-NativeSha256 -Path $exe
    $portableExeHash = Get-NativeSha256 -Path $portableExe
    if ($sourceExeHash -cne $portableExeHash) { throw 'PORTABLE_RELEASE_EXE_HASH_CHANGED' }
    # The EXE executes from the extracted location, not a source/build folder.
    $portableNative = Join-Path $results 'PORTABLE_EXE_NATIVE_3D.json'
    $portableNativeArgs = @('--native-selfcheck','--extended','--report',('"'+$portableNative+'"'))
    $portableProcess = Start-Process -FilePath $portableExe -ArgumentList $portableNativeArgs -WorkingDirectory $portable -PassThru
    if (!$portableProcess.WaitForExit(120000)) {
        Stop-Process -Id $portableProcess.Id -Force -ErrorAction SilentlyContinue
        throw 'PORTABLE_NATIVE_3D_TIMEOUT'
    }
    if ($portableProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $portableNative)) {
        throw 'PORTABLE_NATIVE_3D_FAILED'
    }
    $pNative = Get-Content -Raw -LiteralPath $portableNative -Encoding UTF8 | ConvertFrom-Json
    if ($pNative.result -ne 'PASS_NATIVE_READONLY_SELF_CHECK' -or
        !$pNative.packagedRuntimeVerified -or $pNative.runtimeMode -ne 'PACKAGED_ONEDIR' -or
        $pNative.extendedScene.cells -ne 3128 -or
        !$pNative.extendedScene.cameraPixelChangeVerified -or
        !$pNative.extendedScene.fullResolutionRestored -or
        $pNative.worldWrites -ne 0 -or $pNative.worldContacts -ne 0) {
        throw 'PORTABLE_NATIVE_3D_EVIDENCE_INVALID'
    }
    $summary.portableZipNativeSelfCheck = 'PASS_EXTRACTED_EXE_SYNTHETIC_3128'
    $summary.stage = 'PORTABLE_EXE_PINNED_SOURCE_NATIVE_GUI'
    $portableSmoke = Join-Path $results 'PORTABLE_EXE_B143_GUI.json'
    $portableSmokeArgs = @('--smoke','--smoke-engine',('"'+$engine+'"'),'--smoke-report',('"'+$portableSmoke+'"'))
    $portableGui = Start-Process -FilePath $portableExe -ArgumentList $portableSmokeArgs -WorkingDirectory $portable -PassThru
    if (!$portableGui.WaitForExit(120000)) {
        Stop-Process -Id $portableGui.Id -Force -ErrorAction SilentlyContinue
        throw 'PORTABLE_NATIVE_B143_GUI_TIMEOUT'
    }
    if ($portableGui.ExitCode -ne 0 -or !(Test-Path -LiteralPath $portableSmoke)) {
        throw 'PORTABLE_NATIVE_B143_GUI_FAILED'
    }
    $pSource = Get-Content -Raw -LiteralPath $portableSmoke -Encoding UTF8 | ConvertFrom-Json
    if ($pSource.result -ne 'PASS' -or $pSource.sourceVoxels -ne 917 -or
        $pSource.guiRenderer -ne 'VTK_NATIVE_EMBEDDED' -or !$pSource.nativeScenePixelsVerified -or
        $pSource.nativeCells -ne 917 -or $pSource.worldWrites -ne 0 -or
        $pSource.worldContacts -ne 0) { throw 'PORTABLE_NATIVE_B143_GUI_EVIDENCE_INVALID' }
    $summary.portableZipSourceGui = 'PASS_EXTRACTED_EXE_PINNED_B143_NATIVE_GUI'
    if ($SavedRevisionId) {
        # Optional REAL saved revision (NOT model inference): the exact EXTRACTED
        # EXE reads the user's immutable SOURCE snapshot without switching head.
        $summary.stage = 'EXTRACTED_EXE_SAVED_REVISION_NATIVE_3D'
        $savedPath = Join-Path $results 'SAVED_REVISION_NATIVE_3D.json'
        $savedArgs = @('--saved-native-selfcheck','--revision-id',$SavedRevisionId,
                       '--report',('"'+$savedPath+'"'))
        $savedProcess = Start-Process -FilePath $portableExe -ArgumentList $savedArgs -WorkingDirectory $portable -PassThru
        if (!$savedProcess.WaitForExit(120000)) {
            Stop-Process -Id $savedProcess.Id -Force -ErrorAction SilentlyContinue
            throw 'SAVED_NATIVE_REVISION_TIMEOUT'
        }
        if ($savedProcess.ExitCode -ne 0 -or !(Test-Path -LiteralPath $savedPath)) {
            throw 'SAVED_NATIVE_REVISION_FAILED'
        }
        $saved = Get-Content -Raw -LiteralPath $savedPath -Encoding UTF8 | ConvertFrom-Json
        if ($saved.result -ne 'PASS_SAVED_REVISION_NATIVE_RENDER_ONLY' -or
            $saved.revisionId -cne $SavedRevisionId -or !$saved.savedRevisionIntegrityVerified -or
            !$saved.revisionAndIndexUnchanged -or !$saved.cameraPixelChangeVerified -or
            !$saved.panPixelChangeVerified -or
            !$saved.packagedRuntimeVerified -or $saved.cells -lt 1 -or
            !$saved.rendererDiagnostics.classification -or
            $saved.interactionTimingsMs.cameraRotation.samples -ne 12 -or
            $saved.interactionTimingsMs.zoom.samples -ne 12 -or
            $saved.interactionTimingsMs.pan.samples -ne 12 -or
            $saved.idle.renderFrames -ne 0 -or
            $null -eq $saved.workingSetMb.afterInteractions -or
            $saved.modelRequests -ne 0 -or $saved.worldWrites -ne 0 -or
            $saved.minecraftWorldContacts -ne 0 -or $saved.constructionApproved) {
            throw 'SAVED_NATIVE_REVISION_EVIDENCE_INVALID'
        }
        $summary.savedRevisionNative3D = 'PASS_EXTRACTED_EXE_READONLY_SAVED_REVISION'
        $summary.savedRevisionCells = $saved.cells
        $summary.savedRevisionId = $SavedRevisionId
        $summary.savedRevisionRendererDiagnostics = $saved.rendererDiagnostics
        $summary.savedRevisionInteractionTimingsMs = $saved.interactionTimingsMs
        $summary.savedRevisionIdleFrames = $saved.idle.renderFrames
        $summary.savedRevisionWorkingSetMb = $saved.workingSetMb
        $summary.hardwareGpuIndependentlyVerified = $saved.hardwareGpuIndependentlyVerified
    }
    # Avoid keeping a second large runtime copy after the evidence is saved.
    Remove-Item -LiteralPath $portable -Recurse -Force
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
    if ($summary.outcome -eq 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY') {
        Write-Host 'NOTE: Real local model was NOT tested. Use RUN_REAL_MODEL_CHECK.bat for one live request WITHOUT rebuilding.'
    }
    if ($summary.error) { Write-Host ('Error: ' + $summary.error) }
    Remove-Item Env:MAB_STUDIO_NO_PAUSE -ErrorAction SilentlyContinue
    # Working files are isolated and disposable; do not alter original Studio.
    if (Test-Path -LiteralPath (Join-Path $results 'portable-extracted-qa')) {
        Remove-Item -LiteralPath (Join-Path $results 'portable-extracted-qa') -Recurse -Force -ErrorAction SilentlyContinue
    }
    Set-Location -LiteralPath $source
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
if ($summary.outcome -eq 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY') { exit 0 }
exit 1
