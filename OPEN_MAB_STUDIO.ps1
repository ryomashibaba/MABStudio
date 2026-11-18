# MAB Studio v0.3: fast launch of a previously QA-accepted Windows executable.
# NO build, dependency installation, model request, Minecraft contact, or source mutation.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'GET_STUDIO_SOURCE_FINGERPRINT.ps1')
$studioRoot = Join-Path $env:LOCALAPPDATA 'MABStudio'
$diagnostics = Join-Path $studioRoot 'diagnostics'
$cacheBase = Join-Path $studioRoot 'app-runtime'
try {
    $sourceHash = Get-StudioSourceFingerprint -Root $PSScriptRoot
    if (!(Test-Path -LiteralPath $diagnostics -PathType Container)) {
        throw 'NO_WINDOWS_QA: run RUN_WINDOWS_NATIVE_QA.bat once to build a verified EXE.'
    }
    $selected = $null
    $qaFolders = @(Get-ChildItem -LiteralPath $diagnostics -Directory -Filter 'windows-native-*' |
                   Sort-Object LastWriteTime -Descending)
    foreach ($folder in $qaFolders) {
        $qaPath = Join-Path $folder.FullName 'WINDOWS_NATIVE_QA.json'
        if (!(Test-Path -LiteralPath $qaPath -PathType Leaf)) { continue }
        try {
            $qa = Get-Content -LiteralPath $qaPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($qa.schema -cne 'mab-studio-windows-native-acceptance-v1' -or
                $qa.outcome -cne 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY' -or
                $qa.stage -cne 'COMPLETE' -or
                [string]$qa.sourceFingerprintSha256 -cne $sourceHash -or
                [string]$qa.previewZipSha256 -cnotmatch '^[0-9a-f]{64}$') { continue }
            $zip = Join-Path $folder.FullName 'MABStudio-Windows-Native-Preview.zip'
            if (!(Test-Path -LiteralPath $zip -PathType Leaf)) { continue }
            if ([System.IO.Path]::GetFullPath([string]$qa.previewZip) -cne
                [System.IO.Path]::GetFullPath($zip)) { continue }
            $selected = @{ Zip = $zip; Sha = [string]$qa.previewZipSha256; Qa = $qaPath }
            break
        } catch { continue }
    }
    if ($null -eq $selected) {
        throw 'NO_EXACT_SOURCE_QA: no successful Windows EXE matches this Studio source.'
    }
    $version = $sourceHash.Substring(0,12) + '-' + $selected.Sha.Substring(0,12)
    $cache = Join-Path $cacheBase $version
    $exe = Join-Path $cache 'MABStudio\MABStudio.exe'
    $receiptPath = Join-Path $cache 'FAST_START_RECEIPT.json'
    $valid = $false
    if ((Test-Path -LiteralPath $exe -PathType Leaf) -and
        (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        try {
            $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $valid = ($receipt.sourceFingerprintSha256 -ceq $sourceHash -and
                      $receipt.previewZipSha256 -ceq $selected.Sha -and
                      $receipt.exeSha256 -ceq (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash.ToLowerInvariant() -and
                      (Test-Path -LiteralPath (Join-Path $cache 'MABStudio\node.exe') -PathType Leaf) -and
                      (Test-Path -LiteralPath (Join-Path $cache 'MABStudio\NODE_LICENSE.txt') -PathType Leaf))
        } catch { $valid = $false }
    }
    if (!$valid) {
        if (Test-Path -LiteralPath $cache) {
            throw 'CACHED_EXE_INVALID: existing cached runtime failed verification. Nothing was overwritten.'
        }
        # First launch only: extract a QA-accepted ZIP into a persistent private runtime.
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $selected.Zip).Hash.ToLowerInvariant()
        if ($actual -cne $selected.Sha) { throw 'WINDOWS_QA_ZIP_HASH_MISMATCH' }
        New-Item -ItemType Directory -Force -Path $cacheBase | Out-Null
        $tmp = Join-Path $cacheBase ('install-' + [Guid]::NewGuid().ToString('N'))
        try {
            New-Item -ItemType Directory -Path $tmp | Out-Null
            Expand-Archive -LiteralPath $selected.Zip -DestinationPath $tmp
            $newExe = Join-Path $tmp 'MABStudio\MABStudio.exe'
            if (!(Test-Path -LiteralPath $newExe -PathType Leaf) -or
                !(Test-Path -LiteralPath (Join-Path $tmp 'MABStudio\node.exe') -PathType Leaf) -or
                !(Test-Path -LiteralPath (Join-Path $tmp 'MABStudio\NODE_LICENSE.txt') -PathType Leaf)) {
                throw 'QA_ARCHIVE_MISSING_RUNTIME_FILES'
            }
            $newReceipt = [ordered]@{
                sourceFingerprintSha256 = $sourceHash
                previewZipSha256 = $selected.Sha
                exeSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $newExe).Hash.ToLowerInvariant()
                sourceQa = $selected.Qa
            }
            $newReceipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $tmp 'FAST_START_RECEIPT.json') -Encoding UTF8
            Move-Item -LiteralPath $tmp -Destination $cache
        } finally {
            if (Test-Path -LiteralPath $tmp) {
                Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Write-Host 'Launching verified MAB Studio (no rebuild or AI inference).'
    $proc = Start-Process -FilePath $exe -WorkingDirectory $cache -PassThru
    Start-Sleep -Milliseconds 1200
    $proc.Refresh()
    if ($proc.HasExited) {
        throw ('GUI_EXITED_EARLY: exit code ' + $proc.ExitCode + '. This is a real GUI startup failure; report it.')
    }
    exit 0
} catch {
    Write-Host ('MAB Studio fast launch: FAIL: ' + $_.Exception.Message)
    Write-Host ('Source is unchanged. Windows QA and saved projects were not modified.')
    exit 1
}
