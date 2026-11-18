# One explicit launch: reuse exact-source Windows native PASS or build it once,
# then make ONE genuine loaded-model request and preserve its verified project.
# No per-run consent prompts (project owner approved these tests). No Minecraft I/O.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'GET_STUDIO_SOURCE_FINGERPRINT.ps1')
Set-Location -LiteralPath $PSScriptRoot
$studio = Join-Path $env:LOCALAPPDATA 'MABStudio'
$diagnostics = Join-Path $studio 'diagnostics'
try {
    $choice = Join-Path $studio 'preferences.json'
    if (!(Test-Path -LiteralPath $choice -PathType Leaf)) {
        throw 'MODEL_CHOICE_NOT_SAVED: select the local provider/model in Studio first.'
    }
    $prefs = Get-Content -LiteralPath $choice -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($prefs.schema -cne 'mab-studio-local-interface-preferences-v1' -or
        @('LM Studio','Ollama') -cnotcontains [string]$prefs.provider -or
        [string]$prefs.model -cnotmatch '^[A-Za-z0-9_.:/-]{1,180}$') {
        throw 'MODEL_CHOICE_INVALID: choose a valid local model in Studio first.'
    }
    $sourceHash = Get-StudioSourceFingerprint -Root $PSScriptRoot
    $pass = $false
    if (Test-Path -LiteralPath $diagnostics -PathType Container) {
        $qaFolders = @(Get-ChildItem -LiteralPath $diagnostics -Directory -Filter 'windows-native-*' |
                       Sort-Object LastWriteTime -Descending)
        foreach ($folder in $qaFolders) {
            $qaFile = Join-Path $folder.FullName 'WINDOWS_NATIVE_QA.json'
            if (!(Test-Path -LiteralPath $qaFile -PathType Leaf)) { continue }
            try {
                $qa = Get-Content -LiteralPath $qaFile -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($qa.schema -cne 'mab-studio-windows-native-acceptance-v1' -or
                    $qa.outcome -cne 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY' -or
                    $qa.stage -cne 'COMPLETE' -or
                    [string]$qa.sourceFingerprintSha256 -cne $sourceHash) { continue }
                $zipFile = [string]$qa.previewZip
                $expected = Join-Path $folder.FullName 'MABStudio-Windows-Native-Preview.zip'
                if ([System.IO.Path]::GetFullPath($zipFile) -cne
                    [System.IO.Path]::GetFullPath($expected) -or
                    !(Test-Path -LiteralPath $zipFile -PathType Leaf)) { continue }
                $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipFile).Hash.ToLowerInvariant()
                if ($actual -cne [string]$qa.previewZipSha256) { continue }
                $pass = $true
                break
            } catch { continue }
        }
    }
    if (!$pass) {
        Write-Host 'No exact-source Windows native PASS. Building and checking one isolated EXE.'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'RUN_WINDOWS_NATIVE_QA.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'WINDOWS_NATIVE_QA_FAILED: genuine inference not attempted.' }
    } else {
        Write-Host 'Reusing exact-source, SHA-256-verified Windows native PASS. No rebuild.'
    }
    Write-Host 'Running ONE loaded-model acceptance with native 3D and new Studio project save.'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'RUN_REAL_MODEL_CHECK.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'REAL_MODEL_CHECK_FAILED: inspect the run report; no automatic retry.' }
    Write-Host 'PASS: native EXE model->SOURCE->3D->new project. Reopen Studio to review/revise.'
    exit 0
} catch {
    Write-Host ('MAB Studio acceptance: FAIL: ' + $_.Exception.Message)
    exit 1
}
