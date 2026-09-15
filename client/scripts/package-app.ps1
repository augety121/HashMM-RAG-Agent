param(
    [Parameter(Mandatory = $true)][string]$AppRoot,
    [string]$OutputDirectory = "release-app"
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$app = [System.IO.Path]::GetFullPath($AppRoot)
if (-not (Test-Path -LiteralPath (Join-Path $app "gradlew.bat") -PathType Leaf)) {
    throw "Not an Android project: $app"
}
$buildFile = Join-Path $app "app\build.gradle.kts"
$buildText = [System.IO.File]::ReadAllText($buildFile, [System.Text.Encoding]::UTF8)
$versionName = if ($buildText -match 'versionName\s*=\s*"([^"]+)"') { $Matches[1] } else { throw "versionName not found" }
$versionCode = if ($buildText -match 'versionCode\s*=\s*(\d+)') { $Matches[1] } else { throw "versionCode not found" }
$releaseFile = Join-Path $root "hashmm\release-manifest.json"
$releaseManifest = Get-Content -LiteralPath $releaseFile -Raw -Encoding UTF8 | ConvertFrom-Json
$release = [string]$releaseManifest.release
if ($release -notmatch '^V\d+$') { throw "backend release not found in unified manifest" }
if ([string]$releaseManifest.android_version_name -ne $versionName -or
    [int]$releaseManifest.android_version_code -ne [int]$versionCode) {
    throw "Android source version differs from unified release manifest"
}

$releaseApk = Join-Path $app "app\build\outputs\apk\release\app-release-unsigned.apk"
$debugApk = Join-Path $app "app\build\outputs\apk\debug\app-debug.apk"
if (-not (Test-Path -LiteralPath $releaseApk -PathType Leaf)) { throw "Missing release APK: $releaseApk" }
if (-not (Test-Path -LiteralPath $debugApk -PathType Leaf)) { throw "Missing debug APK: $debugApk" }

$output = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
}
New-Item -ItemType Directory -Force -Path $output | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zipName = "hashmm-app-$release-$versionName-$stamp.zip"
$zipPath = Join-Path $output $zipName
$stage = Join-Path $output ".stage-$release-$versionName-$stamp"
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $releaseName = "HashMM-App-$release-$versionName-release-unsigned.apk"
    $debugName = "HashMM-App-$release-$versionName-debug-signed.apk"
    Copy-Item -LiteralPath $releaseApk -Destination (Join-Path $stage $releaseName)
    Copy-Item -LiteralPath $debugApk -Destination (Join-Path $stage $debugName)
    $releaseHash = (Get-FileHash -LiteralPath $releaseApk -Algorithm SHA256).Hash.ToLowerInvariant()
    $debugHash = (Get-FileHash -LiteralPath $debugApk -Algorithm SHA256).Hash.ToLowerInvariant()
    $readme = @(
        "HashMM Android App $release / $versionName ($versionCode)",
        "",
        "$debugName is signed with the Android debug certificate and can be installed for acceptance testing.",
        "$releaseName passed R8, resource shrinking and lintVital, but is unsigned because no publisher keystore was supplied. Sign it with the release publisher key before public distribution.",
        "",
        "SHA-256:",
        "$releaseHash  $releaseName",
        "$debugHash  $debugName"
    ) -join "`n"
    [System.IO.File]::WriteAllText((Join-Path $stage "README.txt"), $readme + "`n", (New-Object System.Text.UTF8Encoding($false)))
    $insideManifest = [ordered]@{
        schema = "hashmm.android-artifacts.v1"
        backend_release = $release
        product_version = [string]$releaseManifest.product_version
        android_version_name = $versionName
        android_version_code = [int]$versionCode
        production_signed = $false
        artifacts = @(
            [ordered]@{ file = $releaseName; sha256 = $releaseHash; distribution = "unsigned_release" },
            [ordered]@{ file = $debugName; sha256 = $debugHash; distribution = "internal_debug" }
        )
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $stage "release-manifest.json"),
        (($insideManifest | ConvertTo-Json -Depth 6) + "`n"),
        (New-Object System.Text.UTF8Encoding($false))
    )
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipPath -CompressionLevel Optimal
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText("$zipPath.sha256", "$hash  $zipName`n", (New-Object System.Text.UTF8Encoding($false)))
$sidecar = [ordered]@{
    schema = "hashmm.release-artifact.v1"
    product = "HashMM Android"
    backend_release = $release
    product_version = [string]$releaseManifest.product_version
    android_version_name = $versionName
    android_version_code = [int]$versionCode
    production_signed = $false
    file = $zipName
    bytes = (Get-Item -LiteralPath $zipPath).Length
    sha256 = $hash
}
[System.IO.File]::WriteAllText(
    "$zipPath.release.json",
    (($sidecar | ConvertTo-Json -Depth 5) + "`n"),
    (New-Object System.Text.UTF8Encoding($false))
)

[pscustomobject]@{
    Release = $release
    Version = "$versionName ($versionCode)"
    Zip = $zipPath
    Bytes = (Get-Item -LiteralPath $zipPath).Length
    SHA256 = $hash
    Checksum = "$zipPath.sha256"
    Manifest = "$zipPath.release.json"
} | Format-List
