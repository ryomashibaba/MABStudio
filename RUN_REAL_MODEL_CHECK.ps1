# MAB Studio local provider acceptance using the LAST PASS Windows EXE.
# No Python installation, EXE rebuild, server starts, model loads or Minecraft commands.
# Fails closed if the saved model is not already running or the QA ZIP hash differs.
. (Join-Path $PSScriptRoot 'GET_STUDIO_SOURCE_FINGERPRINT.ps1')
$ErrorActionPreference = 'Stop'
$studioRoot = Join-Path $env:LOCALAPPDATA 'MABStudio'
$diagnostics = Join-Path $studioRoot 'diagnostics'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$id = [Guid]::NewGuid().ToString('N').Substring(0,8)
$run = Join-Path $diagnostics ('real-model-' + $stamp + '-' + $id)
$extracted = Join-Path $run 'extracted'
$reportPath = Join-Path $run 'REAL_MODEL_ACCEPTANCE.json'
$runSummary = Join-Path $run 'REAL_MODEL_CHECK.json'
New-Item -ItemType Directory -Force -Path $run | Out-Null
$summary = [ordered]@{
    schema = 'mab-studio-real-model-check-v1'
    createdAt = (Get-Date).ToUniversalTime().ToString('o')
    outcome = 'FAIL'
    stage = 'PREFLIGHT'
    realInference = 'NOT_ATTEMPTED'
    worldWrites = 0
    acceptanceReport = $reportPath
}
try {
    $choice = Join-Path $studioRoot 'preferences.json'
    if (!(Test-Path -LiteralPath $choice -PathType Leaf)) {
        throw 'MODEL_CHOICE_NOT_SAVED: select a provider/model in the MAB Studio GUI first.'
    }
    $pref = Get-Content -LiteralPath $choice -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($pref.schema -cne 'mab-studio-local-interface-preferences-v1') {
        throw 'MODEL_PREFERENCES_INVALID'
    }
    $provider = [string]$pref.provider
    $model = [string]$pref.model
    if (@('LM Studio','Ollama') -cnotcontains $provider -or
        $model -cnotmatch '^[A-Za-z0-9_.:/-]{1,180}$') {
        throw 'MODEL_CHOICE_INVALID: choose the loaded model in the GUI first.'
    }
    $summary.provider = $provider
    $summary.model = $model
    $summary.stage = 'VERIFY_ALREADY_LOADED_MODEL'
    $inferenceModel = $model
    if ($provider -ceq 'LM Studio') {
        # /v1/models (the GUI picker) may include downloaded/JIT models.
        # Native /api/v1/models describes actual loaded instances with separate IDs.
        # Only pre-existing LLM instances are eligible; never load a model here.
        $catalog = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:1234/api/v1/models' -TimeoutSec 8
        if ($null -eq $catalog -or $null -eq $catalog.models) {
            throw 'LM_STUDIO_NATIVE_CATALOG_UNAVAILABLE: /api/v1/models did not return a models list.'
        }
        $loadedCandidates = @(
            foreach ($entry in @($catalog.models)) {
                if ($null -eq $entry -or [string]$entry.type -cne 'llm') { continue }
                $key = [string]$entry.key
                foreach ($instance in @($entry.loaded_instances)) {
                    if ($null -eq $instance) { continue }
                    $instanceId = [string]$instance.id
                    if ($instanceId -cnotmatch '^[A-Za-z0-9_.:/-]{1,180}$') { continue }
                    [pscustomobject]@{ id = $instanceId; key = $key }
                }
            }
        )
        $summary.loadedLlmCount = $loadedCandidates.Count
        $summary.loadedInstanceIds = @($loadedCandidates | ForEach-Object { [string]$_.id })
        $summary.savedModel = $model
        $exact = @($loadedCandidates | Where-Object {
            $_.id -ceq $model -or $_.key -ceq $model
        })
        if ($exact.Count -eq 1) {
            $inferenceModel = [string]$exact[0].id
            $summary.modelSelectionMethod = 'EXACT_LOADED_KEY_OR_INSTANCE_ID'
        } elseif ($exact.Count -gt 1) {
            throw 'LM_STUDIO_MULTIPLE_INSTANCES_FOR_SELECTED_MODEL: unload extras or select a unique instance ID.'
        } elseif ($loadedCandidates.Count -eq 1) {
            # A different loaded model is NOT consent to switch inference target.
            # Match the selected model by its native key or loaded instance ID.
            $summary.modelSelectionMethod = 'SELECTED_MODEL_MISMATCH_HOLD'
            $summary.modelSelectionNote = 'Saved GUI selection does not match the loaded LLM; no inference was started. Select its exact key or instance ID and retry with approval.'
            throw ('LM_STUDIO_SELECTED_MODEL_NOT_LOADED: saved=' + $model + '; loaded=' + [string]$loadedCandidates[0].id)
        } elseif ($loadedCandidates.Count -eq 0) {
            throw 'LM_STUDIO_NO_LOADED_LLM: LM Studio server is running but no LLM instance is loaded. Load the desired model in LM Studio first; picking it in Studio is not enough.'
        } else {
            $candidateNames = @($loadedCandidates | Select-Object -First 5 | ForEach-Object {
                [string]$_.id
            })
            $summary.loadedInstanceIds = $candidateNames
            throw ('LM_STUDIO_AMBIGUOUS_LOADED_MODELS: saved model ' + $model +
                   ' does not match any loaded instance; loaded IDs: ' + ($candidateNames -join ', ') +
                   '. Select one of these IDs in MAB Studio (or unload the others).')
        }
        $summary.resolvedModel = $inferenceModel
        Write-Host ('LM Studio loaded LLM verified: ' + $inferenceModel +
                    ' (' + $summary.modelSelectionMethod + ')')
    } else {
        $running = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/ps' -TimeoutSec 8
        $loadedFound = @($running.models | Where-Object {
            $null -ne $_ -and (($_.name -ceq $model) -or ($_.model -ceq $model))
        })
        if ($loadedFound.Count -ne 1) {
            throw 'OLLAMA_MODEL_NOT_RUNNING: load the selected model first; this check will not auto-load it.'
        }
    }
    $summary.stage = 'FIND_MATCHING_SOURCE_PASS_EXE'
    $sourceFingerprint = Get-StudioSourceFingerprint -Root $PSScriptRoot
    $summary.sourceFingerprintSha256 = $sourceFingerprint
    $qaReports = @(Get-ChildItem -Path $diagnostics -Directory -Filter 'windows-native-*' |
                   Sort-Object LastWriteTime -Descending |
                   ForEach-Object {
                       $f = Join-Path $_.FullName 'WINDOWS_NATIVE_QA.json'
                       if (Test-Path -LiteralPath $f -PathType Leaf) { $f }
                   })
    $selected = $null
    foreach ($f in $qaReports) {
        try {
            $qa = Get-Content -LiteralPath $f -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($qa.schema -cne 'mab-studio-windows-native-acceptance-v1' -or
                $qa.outcome -cne 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY' -or
                $qa.stage -cne 'COMPLETE' -or
                [string]$qa.sourceFingerprintSha256 -cne $sourceFingerprint) { continue }
            $zipFile = [string]$qa.previewZip
            $qaFolder = Split-Path -Parent $f
            if ([System.IO.Path]::GetFullPath($zipFile) -cne
                [System.IO.Path]::GetFullPath((Join-Path $qaFolder 'MABStudio-Windows-Native-Preview.zip'))) {
                continue
            }
            if (!(Test-Path -LiteralPath $zipFile -PathType Leaf)) { continue }
            $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipFile).Hash.ToLowerInvariant()
            if ($sha -cne [string]$qa.previewZipSha256) { continue }
            $selected = $zipFile
            $summary.sourceWindowsQa = $f
            $summary.verifiedZipSha256 = $sha
            break
        } catch { continue }
    }
    if (!$selected) {
        throw 'NO_VERIFIED_EXE_ZIP: no Windows PASS package matches these source files. Run RUN_WINDOWS_NATIVE_QA.bat once; older EXEs are not evidence for current Studio.'
    }
    $summary.stage = 'EXTRACT_VERIFIED_EXE'
    New-Item -ItemType Directory -Force -Path $extracted | Out-Null
    Expand-Archive -LiteralPath $selected -DestinationPath $extracted -Force
    $exe = Join-Path $extracted 'MABStudio\MABStudio.exe'
    if (!(Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw 'VERIFIED_ARCHIVE_EXE_MISSING'
    }
    $summary.stage = 'ONE_REAL_MODEL_REQUEST'
    $summary.realInference = 'NOT_ATTEMPTED'
    $requestClock = [System.Diagnostics.Stopwatch]::StartNew()
    # Use acceptance.py's existing Japanese diagnostic brief (no shell Unicode quoting).
    # Model strings were strictly checked above before passing as process arguments.
    $arguments = '--acceptance --save-to-studio --provider "' + $provider + '" --model "' + $inferenceModel + '" --report "' + $reportPath + '"'
    # Bound the wait: even a stuck GUI, transport or runtime must not hang forever.
    # Kill ONLY the temporary acceptance EXE that this script started.
    $proc = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $extracted -PassThru
    $summary.realInference = 'PROCESS_STARTED_INFERENCE_UNCONFIRMED'
    if (!$proc.WaitForExit(1200000)) {
        $requestClock.Stop()
        $summary.elapsedSeconds = [math]::Round($requestClock.Elapsed.TotalSeconds,2)
        $summary.stage = 'ACCEPTANCE_PROCESS_TIMEOUT'
        try { $proc.Kill() } catch { }
        try { [void]$proc.WaitForExit(5000) } catch { }
        throw 'ACCEPTANCE_PROCESS_TIMEOUT: the one real-model acceptance exceeded 20 minutes; no second request was started.'
    }
    $requestClock.Stop()
    $summary.elapsedSeconds = [math]::Round($requestClock.Elapsed.TotalSeconds,2)
    $summary.exitCode = $proc.ExitCode
    if (!(Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw 'REAL_MODEL_REPORT_NOT_CREATED'
    }
    $receipt = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $summary.realInference = 'RECEIPT_READ_INFERENCE_UNCONFIRMED'
    $summary.stage = [string]$receipt.stage
    $summary.outcome = [string]$receipt.outcome
    # Validate the receipt identity and construction boundaries BEFORE success.
    # A normal PRE-REQUEST failure must retain its real stage/error, not be
    # overwritten by a misleading 'receipt inconsistent' message.
    if ($receipt.schema -cne 'mab-studio-local-ai-gui-acceptance-v1' -or
        $receipt.provider -cne $provider -or $receipt.model -cne $inferenceModel -or
        $receipt.testDoubleUsed -or $receipt.worldWrites -ne 0 -or
        $receipt.worldContacts -ne 0 -or $receipt.minecraftEducationTested -or
        $receipt.constructionApproved -or @($receipt.BuildPlan.operations).Count -ne 0) {
        throw 'GENUINE_MODEL_RECEIPT_INCONSISTENT'
    }
    if ($receipt.sourceValid) { $summary.voxels = $receipt.source.voxelCount }
    if ($null -ne $receipt.providerRequestSeconds) { $summary.modelSeconds = $receipt.providerRequestSeconds }
    if ($receipt.failureClass) { $summary.failureClass = [string]$receipt.failureClass }
    if ($receipt.error) { $summary.error = [string]$receipt.error }
    if (!$receipt.actualProviderRequestAttempted) {
        $summary.realInference = 'NOT_ATTEMPTED'
        $summary.outcome = 'FAIL'
        if (!$summary.error) { $summary.error = 'The verified EXE stopped before it called the selected provider.' }
    } elseif ($proc.ExitCode -ne 0 -or $summary.outcome -cne 'PASS_SOURCE_AND_GUI_ONLY') {
        $summary.realInference = 'REQUEST_ATTEMPTED'
        if (!$summary.error) { $summary.error = 'Real model request, source validation or Tk preview failed.' }
    } elseif (!$receipt.sourceValid -or !$receipt.guiRendered -or
        $receipt.guiRenderer -cne 'VTK_NATIVE_EMBEDDED' -or !$receipt.nativeScenePixelsVerified -or
        !$receipt.studioProject.saved -or $receipt.studioProject.worldWrites -ne 0 -or
        $receipt.stage -cne 'COMPLETE') {
        $summary.realInference = 'REQUEST_ATTEMPTED'
        throw 'GENUINE_MODEL_SUCCESS_PROOF_INCOMPLETE'
    } else {
        $summary.realInference = 'REQUEST_ATTEMPTED'
        $summary.stage = 'COMPLETE'
        $summary.savedRevisionId = [string]$receipt.studioProject.revisionId
        $summary.nativeRenderer = [string]$receipt.guiRenderer
        $summary.nativeScenePixelsVerified = [bool]$receipt.nativeScenePixelsVerified
    }
} catch {
    $summary.outcome = 'FAIL'
    $summary.error = [string]$_.Exception.Message
} finally {
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $runSummary -Encoding UTF8
    Write-Host ('MAB Studio REAL model check: ' + $summary.outcome)
    Write-Host ('Stage: ' + $summary.stage)
    if ($summary.error) { Write-Host ('Error: ' + $summary.error) }
    if ($null -ne $summary.elapsedSeconds) { Write-Host ('Real-model check elapsed seconds: ' + $summary.elapsedSeconds) }
    if ($summary.voxels) { Write-Host ('B143 source cells: ' + $summary.voxels) }
    Write-Host ('Report: ' + $runSummary)
    # The passed EXE always remains available in its original verified ZIP.
    if (Test-Path -LiteralPath $extracted) {
        Remove-Item -LiteralPath $extracted -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if ($summary.outcome -ceq 'PASS_SOURCE_AND_GUI_ONLY') { exit 0 }
exit 1
