# Shared Windows PS 5.1 fingerprint for the app actually packaged in the EXE.
# Ignore build caches and generated node_modules; include code, embedded assets,
# and the two build-input scripts. No writes or network requests.
function Get-StudioFileSha256 {
    param([Parameter(Mandatory=$true)][string]$Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try { return [System.BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
function Get-StudioSourceFingerprint {
    param([Parameter(Mandatory=$true)][string]$Root)
    $base = (Resolve-Path -LiteralPath $Root).ProviderPath.TrimEnd('\','/')
    $appPath = Join-Path $base 'app'
    if (!(Test-Path -LiteralPath $appPath -PathType Container)) {
        throw 'STUDIO_SOURCE_DIRECTORY_MISSING'
    }
    $files = @(Get-ChildItem -LiteralPath $appPath -Recurse -File | Where-Object {
        $_.FullName -notmatch '[\\/](?:__pycache__|node_modules|\.pytest_cache)[\\/]' -and
        @('.pyc','.pyo') -cnotcontains $_.Extension
    })
    foreach ($name in @('build_windows_native.bat','prepare_education_readonly.bat')) {
        $file = Join-Path $base $name
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw ('BUILD_INPUT_MISSING: '+$name) }
        $files += Get-Item -LiteralPath $file
    }
    $items = @(
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($base.Length+1).Replace('\','/')
            $hash = Get-StudioFileSha256 -Path $file.FullName
            $relative + '|' + $hash
        }
    ) | Sort-Object -CaseSensitive
    $body = [string]::Join("`n", [string[]]@($items))
    $algo = [System.Security.Cryptography.SHA256]::Create()
    try { $bytes = $algo.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($body)) }
    finally { $algo.Dispose() }
    return [System.BitConverter]::ToString($bytes).Replace('-','').ToLowerInvariant()
}
