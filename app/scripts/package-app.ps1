param(
    [string]$OutputDirectory = "release-app",
    [ValidateSet("debug", "release")]
    [string]$Variant = "debug",
    [string]$ReleaseManifest = "..\hashmm\hashmm\release-manifest.json"
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$metadataPath = Join-Path $root "app\build\outputs\apk\$Variant\output-metadata.json"
if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
    $task = if ($Variant -eq "release") { "app:assembleRelease" } else { "app:assembleDebug" }
    throw "$Variant APK metadata is missing. Run .\gradlew.bat $task first."
}

$metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
$element = @($metadata.elements)[0]
if ($null -eq $element -or [string]::IsNullOrWhiteSpace([string]$element.outputFile)) {
    throw "$Variant APK metadata has no output file."
}

$source = Join-Path (Split-Path -Parent $metadataPath) ([string]$element.outputFile)
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "$Variant APK is missing: $source"
}
if ($Variant -eq "release" -and ([System.IO.Path]::GetFileName($source) -match '(?i)unsigned')) {
    throw "Release APK is unsigned. Configure the Android release signing environment before packaging."
}

$outputRoot = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
}
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$versionName = [string]$element.versionName
$versionCode = [int]$element.versionCode
$manifestPath = if ([System.IO.Path]::IsPathRooted($ReleaseManifest)) {
    [System.IO.Path]::GetFullPath($ReleaseManifest)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $root $ReleaseManifest))
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Unified release manifest is missing: $manifestPath"
}
$release = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$release.android_version_name -ne $versionName -or [int]$release.android_version_code -ne $versionCode) {
    throw "APK version $versionName/$versionCode does not match unified release manifest $($release.android_version_name)/$($release.android_version_code)."
}
$name = "HashMM-App-$versionName-$versionCode-$Variant.apk"
$destination = Join-Path $outputRoot $name
Copy-Item -LiteralPath $source -Destination $destination -Force

$hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
$shaPath = "$destination.sha256"
[System.IO.File]::WriteAllText(
    $shaPath,
    "$hash  $name`n",
    (New-Object System.Text.UTF8Encoding($false))
)

$releaseJsonPath = [System.IO.Path]::ChangeExtension($destination, ".release.json")
$artifact = [ordered]@{
    schema = "hashmm.android-artifact.v1"
    product_version = [string]$release.product_version
    android_version = $versionName
    version_code = $versionCode
    backend_release = [string]$release.release
    protocols = $release.protocols
    variant = $Variant
    artifact = $name
    bytes = (Get-Item -LiteralPath $destination).Length
    sha256 = $hash
}
[System.IO.File]::WriteAllText(
    $releaseJsonPath,
    (($artifact | ConvertTo-Json -Depth 8) + "`n"),
    (New-Object System.Text.UTF8Encoding($false))
)

[pscustomobject]@{
    Variant = $Variant
    VersionName = $versionName
    VersionCode = $versionCode
    APK = $destination
    Bytes = (Get-Item -LiteralPath $destination).Length
    SHA256 = $hash
    Checksum = $shaPath
    ReleaseManifest = $releaseJsonPath
} | Format-List
