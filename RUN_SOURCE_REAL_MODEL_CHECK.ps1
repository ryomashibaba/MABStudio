# Source-level real-model verification. No EXE rebuild or PyInstaller installs.
# Only an already loaded local model. No Minecraft or model configuration changes.
$ErrorActionPreference = 'Stop'
$studioRoot = Join-Path $env:LOCALAPPDATA 'MABStudio'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dir = Join-Path $studioRoot ('diagnostics\source-model-' + $stamp)
New-Item -Force -ItemType Directory -Path $dir | Out-Null
$report = Join-Path $dir 'SOURCE_REAL_MODEL_ACCEPTANCE.json'
$stage = 'PREFLIGHT'
try {
    Set-Location -LiteralPath $PSScriptRoot
    if (!(Test-Path -LiteralPath 'app\acceptance_cli.py' -PathType Leaf)) {
        throw 'SOURCE_FILES_MISSING: extract the entire new MAB Studio source ZIP.'
    }
    $python = (Get-Command python -ErrorAction Stop).Source
    & $python --version
    if ($LASTEXITCODE -ne 0) { throw 'PYTHON_NOT_WORKING' }
    $node = (Get-Command node -ErrorAction Stop).Source
    $nodeVersion = (& $node --version)
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v(2[2-9]|[3-9][0-9])\.') {
        throw 'NODE_22_OR_LATER_REQUIRED_FOR_PINNED_SOURCE'
    }
    $prefFile = Join-Path $studioRoot 'preferences.json'
    if (!(Test-Path -LiteralPath $prefFile -PathType Leaf)) {
        throw 'STUDIO_PREFERENCES_NOT_FOUND: open Studio and choose a local provider.'
    }
    $pref = Get-Content -Raw -Encoding UTF8 -LiteralPath $prefFile | ConvertFrom-Json
    if ($pref.schema -cne 'mab-studio-local-interface-preferences-v1') {
        throw 'STUDIO_PREFERENCES_INVALID'
    }
    $provider = [string]$pref.provider
    $model = [string]$pref.model
    if (@('LM Studio','Ollama') -cnotcontains $provider -or
        $model -cnotmatch '^[A-Za-z0-9_.:/-]{1,180}$') {
        throw 'INVALID_SAVED_MODEL'
    }
    $stage = 'VERIFY_LOADED_MODEL'
    if ($provider -ceq 'LM Studio') {
        $catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:1234/api/v1/models' -Method Get -TimeoutSec 8
        $loaded = @(
            foreach ($entry in @($catalog.models)) {
                if ($null -eq $entry -or [string]$entry.type -cne 'llm') { continue }
                foreach ($ins in @($entry.loaded_instances)) {
                    if ($null -eq $ins) { continue }
                    $id = [string]$ins.id
                    if ($id -cmatch '^[A-Za-z0-9_.:/-]{1,180}$') {
                        [pscustomobject]@{id=$id; key=[string]$entry.key}
                    }
                }
            }
        )
        $exact = @($loaded | Where-Object { $_.id -ceq $model -or $_.key -ceq $model })
        if ($exact.Count -eq 1) { $model = [string]$exact[0].id }
        elseif ($exact.Count -eq 0 -and $loaded.Count -eq 1) {
            $model = [string]$loaded[0].id
            Write-Warning 'GUI model differs from sole loaded LM Studio model. Using the actually loaded instance.'
        } else {
            throw ('NO_UNAMBIGUOUS_LOADED_LM: loaded=' + $loaded.Count + '; exact=' + $exact.Count)
        }
    } else {
        $ps = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/ps' -Method Get -TimeoutSec 8
        $matched = @($ps.models | Where-Object { $null -ne $_ -and ($_.name -ceq $model -or $_.model -ceq $model) })
        if ($matched.Count -ne 1) { throw 'OLLAMA_MODEL_NOT_UNIQUELY_RUNNING' }
    }
    $stage = 'RUN_ONE_SOURCE_ACCEPTANCE'
    Write-Host ('Verified loaded model: ' + $model)
    Write-Host 'Testing new compact design format without rebuilding the EXE.'
    & $python -m app.acceptance_cli --acceptance --console-progress --provider $provider --model $model --report $report
    $code = $LASTEXITCODE
    if (!(Test-Path -LiteralPath $report -PathType Leaf)) { throw 'REPORT_NOT_WRITTEN' }
    $data = Get-Content -Raw -Encoding UTF8 -LiteralPath $report | ConvertFrom-Json
    Write-Host ('Real model stage: ' + $data.stage)
    Write-Host ('Real model outcome: ' + $data.outcome)
    if ($data.error) { Write-Host ('Error: ' + $data.error) }
    if ($data.source.voxelCount) { Write-Host ('B143 cells: ' + $data.source.voxelCount) }
    Write-Host ('Report: ' + $report)
    if ($code -ne 0 -or $data.outcome -cne 'PASS_SOURCE_AND_GUI_ONLY') { exit 1 }
    exit 0
} catch {
    Write-Host ('Source model check: FAIL at ' + $stage)
    Write-Host ('Error: ' + $_.Exception.Message)
    Write-Host ('Report directory: ' + $dir)
    exit 1
}
