# MAB Studio v0.3 -- local trusted-feed update bootstrap (PowerShell 5.1).
# A sync client (for example Google Drive for desktop) supplies the feed.
# Never downloads arbitrary executable code, changes the pinned B143 engine,
# accepts untested builds, or modifies stored projects or Minecraft worlds.
param([switch]$ChooseFeed)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'GET_STUDIO_SOURCE_FINGERPRINT.ps1')
$studioData = Join-Path $env:LOCALAPPDATA 'MABStudio'
$updateRoot = Join-Path $studioData 'auto-update'
$feedConfig = Join-Path $updateRoot 'trusted-feed.txt'
$activePath = Join-Path $updateRoot 'active.json'
$sourceRoot = Join-Path $updateRoot 'sources'
$failedPath = Join-Path $updateRoot 'failed.json'
$feedName = 'MABStudio_Work_Handoff_20261118'

function Resolve-TrustedFeed {
    if (Test-Path -LiteralPath $feedConfig -PathType Leaf) {
        $path = ([System.IO.File]::ReadAllText($feedConfig, [System.Text.Encoding]::UTF8)).Trim()
        if ($path -and (Test-Path -LiteralPath $path -PathType Container)) {
            return (Resolve-Path -LiteralPath $path).ProviderPath
        }
    }
    # Only predictable Google Drive for desktop mount points are considered.
    # No drive-wide recursive search and no publicly shared Drive links.
    $candidates = @()
    foreach ($drive in @(Get-PSDrive -PSProvider FileSystem)) {
        foreach ($segment in @('My Drive','マイドライブ')) {
            $candidates += Join-Path (Join-Path $drive.Root $segment) $feedName
        }
    }
    foreach ($base in @((Join-Path $env:USERPROFILE 'My Drive'),
                       (Join-Path $env:USERPROFILE 'Google Drive'))) {
        $candidates += Join-Path $base $feedName
    }
    foreach ($candidate in $candidates) {
        if ((Test-Path -LiteralPath $candidate -PathType Container) -and
            (Test-Path -LiteralPath (Join-Path $candidate 'STUDIO_RELEASE.json') -PathType Leaf)) {
            New-Item -ItemType Directory -Force -Path $updateRoot | Out-Null
            $selected = (Resolve-Path -LiteralPath $candidate).ProviderPath
            [System.IO.File]::WriteAllText($feedConfig,$selected,[System.Text.Encoding]::UTF8)
            return $selected
        }
    }
    return $null
}

function Read-ActiveSource {
    if (!(Test-Path -LiteralPath $activePath -PathType Leaf)) { return $PSScriptRoot }
    try {
        $current = Get-Content -LiteralPath $activePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$current.schema -cne 'mab-studio-auto-update-state-v1' -or
            [string]$current.releaseSha -cnotmatch '^[a-f0-9]{40}$') { return $PSScriptRoot }
        $allowed = [System.IO.Path]::GetFullPath((Join-Path $sourceRoot ([string]$current.releaseSha)))
        if ([System.IO.Path]::GetFullPath([string]$current.sourcePath) -cne $allowed -or
            !(Test-Path -LiteralPath (Join-Path $allowed 'OPEN_MAB_STUDIO.ps1') -PathType Leaf) -or
            !(Test-Path -LiteralPath (Join-Path $allowed 'RUN_WINDOWS_NATIVE_QA.bat') -PathType Leaf)) {
            return $PSScriptRoot
        }
        return $allowed
    } catch { return $PSScriptRoot }
}

function Test-ReleasePackage {
    param([string]$Path, [string]$ReleaseSha)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $prefix = 'MABStudio_v03_' + $ReleaseSha.Substring(0,7) + '/'
        $files = 0
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            if (!$name.StartsWith($prefix,[StringComparison]::Ordinal) -or
                $name.Contains('\') -or $name.Contains(':') -or
                $name.StartsWith('/') -or
                @($name.Split('/')) -contains '..' -or
                @($name.Split('/')) -contains '.') {
                throw 'UNSAFE_STUDIO_RELEASE_ARCHIVE'
            }
            if ($entry.Length -gt 16MB) { throw 'RELEASE_ENTRY_TOO_LARGE' }
            if (!$name.EndsWith('/')) { $files++ }
        }
        if ($files -lt 30 -or $files -gt 800) { throw 'RELEASE_FILE_COUNT_INVALID' }
    } finally { $zip.Dispose() }
}

function Try-StageUpdate {
    param([string]$Feed, [string]$Current)
    $manifestPath = Join-Path $Feed 'STUDIO_RELEASE.json'
    if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $Current }
    if ((Get-Item -LiteralPath $manifestPath).Length -gt 4096) { throw 'RELEASE_MANIFEST_TOO_LARGE' }
    $release = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $sha = [string]$release.releaseSha
    $filename = [string]$release.archiveName
    $checksum = [string]$release.archiveSha256
    $sequenceText = [string]$release.releaseSequence
    if ([string]$release.schema -cne 'mab-studio-source-release-v1' -or
        $sha -cnotmatch '^[a-f0-9]{40}$' -or
        $filename -cne ('MABStudio_v03_' + $sha.Substring(0,7) + '_Windows_Update_Once.zip') -or
        $checksum -cnotmatch '^[a-f0-9]{64}$' -or
        $sequenceText -cnotmatch '^[0-9]{10,15}$') { throw 'RELEASE_MANIFEST_INVALID' }
    $sequence = [Int64]::Parse($sequenceText)
    if (Test-Path -LiteralPath $activePath -PathType Leaf) {
        try {
            $currentRelease = Get-Content -LiteralPath $activePath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([string]$currentRelease.schema -ceq 'mab-studio-auto-update-state-v1' -and
                $sequence -le [Int64]$currentRelease.releaseSequence) { return $Current }
        } catch { }
    }
    if (Test-Path -LiteralPath $failedPath -PathType Leaf) {
        try {
            $failed = Get-Content -LiteralPath $failedPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($failed.releaseSha -ceq $sha -and
                ((Get-Date).ToUniversalTime() - [DateTime]::Parse([string]$failed.failedAt).ToUniversalTime()).TotalHours -lt 12) {
                return $Current
            }
        } catch { }
    }
    $destination = Join-Path $sourceRoot $sha
    if ([System.IO.Path]::GetFullPath($Current) -ceq [System.IO.Path]::GetFullPath($destination)) { return $Current }
    $archive = Join-Path $Feed $filename
    if (!(Test-Path -LiteralPath $archive -PathType Leaf)) { return $Current }
    if ((Get-Item -LiteralPath $archive).Length -gt 32MB) { throw 'RELEASE_ARCHIVE_TOO_LARGE' }
    if ((Get-StudioFileSha256 -Path $archive) -cne $checksum) { throw 'RELEASE_SHA256_MISMATCH' }
    Test-ReleasePackage -Path $archive -ReleaseSha $sha
    New-Item -ItemType Directory -Force -Path $sourceRoot | Out-Null
    if (!(Test-Path -LiteralPath $destination -PathType Container)) {
        $staging = Join-Path $updateRoot ('stage-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        try {
            Expand-Archive -LiteralPath $archive -DestinationPath $staging
            $inner = Join-Path $staging ('MABStudio_v03_' + $sha.Substring(0,7))
            foreach ($required in @('app\main.py','OPEN_MAB_STUDIO.ps1','RUN_WINDOWS_NATIVE_QA.bat',
                                    'GET_STUDIO_SOURCE_FINGERPRINT.ps1','AUTO_UPDATE_STUDIO.ps1')) {
                if (!(Test-Path -LiteralPath (Join-Path $inner $required) -PathType Leaf)) {
                    throw ('RELEASE_REQUIRED_FILE_MISSING: ' + $required)
                }
            }
            Move-Item -LiteralPath $inner -Destination $destination
        } finally {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    # Do not execute a different build during active local-model inference.
    # The updater runs only before the GUI starts; validation is isolated.
    $fingerprint = Get-StudioSourceFingerprint -Root $destination
    Write-Host ('[MAB Studio] Verifying new source ' + $sha.Substring(0,12) + ' with Windows QA...')
    $env:MAB_STUDIO_NO_PAUSE = '1'
    # A native .bat emits stdout to PowerShell's success pipeline. If this
    # output escapes the function, $launchFrom becomes an array of log lines
    # instead of the accepted source directory. Keep QA visible, not returned.
    & (Join-Path $destination 'RUN_WINDOWS_NATIVE_QA.bat') | Out-Host
    if ($LASTEXITCODE -ne 0) {
        [ordered]@{releaseSha=$sha;failedAt=(Get-Date).ToUniversalTime().ToString('o')} |
            ConvertTo-Json | Set-Content -LiteralPath $failedPath -Encoding UTF8
        throw 'WINDOWS_QA_FAILED_FOR_NEW_RELEASE'
    }
    $qaFolders = @(Get-ChildItem -LiteralPath (Join-Path $studioData 'diagnostics') -Directory -Filter 'windows-native-*' |
                   Sort-Object LastWriteTime -Descending)
    $accepted = $false
    foreach ($folder in $qaFolders) {
        $receipt = Join-Path $folder.FullName 'WINDOWS_NATIVE_QA.json'
        if (!(Test-Path -LiteralPath $receipt -PathType Leaf)) { continue }
        try {
            $result = Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($result.outcome -ceq 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY' -and
                $result.stage -ceq 'COMPLETE' -and
                $result.sourceFingerprintSha256 -ceq $fingerprint) { $accepted = $true; break }
        } catch { continue }
    }
    if (!$accepted) {
        [ordered]@{releaseSha=$sha;failedAt=(Get-Date).ToUniversalTime().ToString('o')} |
            ConvertTo-Json | Set-Content -LiteralPath $failedPath -Encoding UTF8
        throw 'WINDOWS_QA_RECEIPT_MISSING_FOR_NEW_RELEASE'
    }
    $pointer = [ordered]@{schema='mab-studio-auto-update-state-v1';releaseSha=$sha;releaseSequence=$sequence;
                           sourcePath=$destination;sourceFingerprintSha256=$fingerprint}
    $tempPath = Join-Path $updateRoot ('active-' + [Guid]::NewGuid().ToString('N') + '.json')
    $pointer | ConvertTo-Json | Set-Content -LiteralPath $tempPath -Encoding UTF8
    Move-Item -LiteralPath $tempPath -Destination $activePath -Force
    if (Test-Path -LiteralPath $failedPath -PathType Leaf) {
        Remove-Item -LiteralPath $failedPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host ('[MAB Studio] Update accepted: ' + $sha.Substring(0,12))
    return $destination
}

if ($ChooseFeed) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Google Driveで同期されるMABStudio_Work_Handoff_20261118フォルダを選択'
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        New-Item -ItemType Directory -Force -Path $updateRoot | Out-Null
        [System.IO.File]::WriteAllText($feedConfig,$dialog.SelectedPath,[System.Text.Encoding]::UTF8)
        Write-Host '[MAB Studio] Trusted update folder recorded. No update installed yet.'
    }
    exit 0
}

$launchFrom = Read-ActiveSource
try {
    $feed = Resolve-TrustedFeed
    if ($feed) {
        New-Item -ItemType Directory -Force -Path $updateRoot | Out-Null
        $launchFrom = Try-StageUpdate -Feed $feed -Current $launchFrom
    }
} catch {
    Write-Host ('[MAB Studio] Automatic update skipped: ' + $_.Exception.Message)
    Write-Host '[MAB Studio] Previously accepted version remains available.'
}
& (Join-Path $launchFrom 'OPEN_MAB_STUDIO.ps1')
exit $LASTEXITCODE
