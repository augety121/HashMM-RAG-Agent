param(
    [string]$OutputDirectory = "release-server"
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$outputRoot = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
}

$releaseFile = Join-Path $root "hashmm\release-manifest.json"
if (-not (Test-Path -LiteralPath $releaseFile -PathType Leaf)) {
    throw "Unified release manifest is missing: $releaseFile"
}
$releaseManifest = Get-Content -LiteralPath $releaseFile -Raw -Encoding UTF8 | ConvertFrom-Json
$release = [string]$releaseManifest.release
if ($release -notmatch '^V\d+$') { throw "Invalid release value in $releaseFile" }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zipName = "hashmm-server-$release-$stamp.zip"
$zipPath = Join-Path $outputRoot $zipName

$includeDirectories = @(
    "hashmm",
    "sql",
    "scripts",
    "skills",
    "plugins",
    "clients",
    "tools",
    "integrations",
    "deploy",
    "docs",
    "contracts"
)
$currentNoteName = (-join @([char]0x672c, [char]0x8f6e, [char]0x8bf4, [char]0x660e)) + "-$release.md"
$includeRootFiles = @(
    ".env.example",
    "CONFIG.md",
    "HASHMM.md.example",
    "LICENSE",
    "README.md",
    "hashmm-start.sh",
    "start-hashmm.sh",
    "start-hashmm1.sh",
    "CHANGELOG-3-V230-CURRENT.md",
    $currentNoteName,
    "pyproject.toml",
    "requirements.txt",
    "requirements-optional.txt",
    "ruff.toml"
)

$blockedSegments = @(
    ".git", ".agents", ".codex", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "__pycache__", "node_modules", ".next", ".gradle", ".wrangler", ".wrangler-dry-run", "build", "dist",
    "frontend-next", "desktop", "installer-native", "tests", "data", "logs",
    "models", "indexes", "rag_storage", "memory", "checkpoints", "output", "patches"
)
$blockedNames = @(
    ".env", ".env.local", ".env.production", ".env.development",
    "hashmm.sqlite", "hashmm.sqlite.snapshot", "idempotency.db", "image_store.db"
)
$blockedExtensions = @(
    ".pyc", ".pyo", ".log", ".sqlite", ".sqlite3", ".db", ".zip", ".7z", ".rar",
    ".apk", ".aab", ".exe", ".msi", ".dmg", ".pem", ".key", ".p12", ".pfx",
    ".jks", ".keystore"
)

function Test-PackageFile([System.IO.FileInfo]$File) {
    $relative = $File.FullName.Substring($root.Length).TrimStart('\', '/')
    $normalizedRelative = $relative.Replace('\', '/').ToLowerInvariant()
    if ($normalizedRelative.StartsWith("deploy/turn/runtime/")) { return $false }
    $segments = $relative -split '[\\/]'
    foreach ($segment in $segments) {
        if ($blockedSegments -contains $segment.ToLowerInvariant()) { return $false }
    }
    if (($blockedNames -contains $File.Name.ToLowerInvariant()) -and
        $normalizedRelative -ne "deploy/turn/.env.example") { return $false }
    if ($blockedExtensions -contains $File.Extension.ToLowerInvariant()) { return $false }
    if ($File.Name -match '(?i)(service[-_]?role|api[-_]?key|secret|token|credential).*(\.txt|\.json|\.yaml|\.yml)$') {
        return $false
    }
    if (($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $false
    }
    return $true
}

$files = New-Object System.Collections.Generic.List[System.IO.FileInfo]
foreach ($directory in $includeDirectories) {
    $path = Join-Path $root $directory
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { continue }
    Get-ChildItem -LiteralPath $path -Recurse -File -Force | ForEach-Object {
        if (Test-PackageFile $_) { $files.Add($_) }
    }
}
foreach ($name in $includeRootFiles) {
    $path = Join-Path $root $name
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        $file = Get-Item -LiteralPath $path
        if (Test-PackageFile $file) { $files.Add($file) }
    }
}

$unique = $files | Sort-Object FullName -Unique
if ($unique.Count -eq 0) { throw "No server files selected from $root" }

$requiredEntries = @(
    $currentNoteName,
    "hashmm/api/routes/ocr.py",
    "hashmm/pipeline/ocr_queue.py",
    "hashmm/pipeline/ocr_provider.py",
    "hashmm/api/routes/projects.py",
    "hashmm/agent/work_runtime.py",
    "hashmm/agent/task_state.py",
    "hashmm/evaluation/harness_levels.py",
    "plugins/ai_news_radar/plugin.json",
    "hashmm/api/platform_protocol.py",
    "hashmm/api/platform_access.py",
    "hashmm/api/routes/public_api.py",
    "hashmm/api/routes/platform_keys.py",
    "hashmm/retrieval/expected_gain.py",
    "hashmm/kg/knowledge_evolution.py",
    "hashmm/evaluation/benchmarks/loho_search.py",
    "docs/PUBLIC_API_V1.md",
    "docs/LOHOSEARCH_AND_KG_EVOLUTION.md",
    "docs/CHAT_AGENT_V810_SPEC.md",
    "docs/API_PLATFORM_V820_SPEC.md",
    "docs/CONTROL_PLANE_SETTINGS_PLUGINS_V900_SPEC.md",
    "docs/CONTINUITY_CONTROL_PLANE_V1000_SPEC.md",
    "docs/RELEASE_NOTES_V1000.md",
    "docs/CONTINUITY_RUNTIME_V1100_SPEC.md",
    "docs/RELEASE_NOTES_V1100.md",
    "docs/HASHMM_V1200_SECURE_REMOTE_RETRIEVAL_SPEC.md",
    "docs/HASHMM_V1300_CLOUDFLARE_COMPUTER_APP2_SPEC.md",
    "docs/HASHMM_V1500_MULTI_USER_RUNTIME_SPEC.md",
    "docs/HASHMM_V1700_CROSS_DEVICE_TRUTH_SPEC.md",
    "docs/HASHMM_V1800_REMOTE_FABRIC_SPEC.md",
    "docs/HASHMM_V1900_WORK_OS_REMOTE_AGENT_CATALOG_SPEC.md",
    "docs/HASHMM_V2000_AGENT_WORKSPACE_REMOTE_FABRIC_SPEC.md",
    "docs/HASHMM_V2500_EVIDENCE_WORKBENCH_SPEC.md",
    "docs/HASHMM_V2600_VERIFIED_WORK_KERNEL_SPEC.md",
    "docs/HASHMM_V2700_AGENT_COMPATIBILITY_RELEASE_SPEC.md",
    "docs/HASHMM_V2800_CONNECTED_WORKBENCH_RELEASE_SPEC.md",
    "docs/HASHMM_V2801_REMOTE_APPROVAL_HOTFIX.md",
    "docs/HASHMM_V2802_REMOTE_TRUST_MEDIA_RECOVERY.md",
    "contracts/hashmm-event-v1.schema.json",
    "contracts/asyncapi.hashmm.v2700.json",
    "contracts/asyncapi.hashmm.v2800.json",
    "contracts/asyncapi.hashmm.v2801.json",
    "contracts/asyncapi.hashmm.v2802.json",
    "scripts/verify-contracts.py",
    "hashmm/release-manifest.json",
    "hashmm/release.py",
    "hashmm/api/provider_fabric.py",
    "hashmm/agent/behavior_kernel.py",
    "hashmm/api/routes/provider_fabric.py",
    "hashmm/api/routes/canvas_ops.py",
    "docs/HASHMM_V2100_REMOTE_HOST_WORKBENCH_SPEC.md",
    "hashmm/api/remote_devices.py",
    "hashmm/workspace_runtime/cloudflare_computer.py",
    "hashmm/workspace_runtime/store.py",
    "hashmm/api/routes/workspace_runtime.py",
    "integrations/cloudflare-computer-worker/package.json",
    "integrations/cloudflare-computer-worker/package-lock.json",
    "integrations/cloudflare-computer-worker/wrangler.jsonc",
    "integrations/cloudflare-computer-worker/src/index.ts",
    "integrations/cloudflare-computer-worker/src/security.ts",
    "hashmm/retrieval_fabric/contracts.py",
    "hashmm/retrieval_fabric/providers.py",
    "hashmm/retrieval_fabric/service.py",
    "hashmm/retrieval_fabric/store.py",
    "deploy/secure-remote/cloudflared-config.yml.example",
    "deploy/secure-remote/hashmm-v1200.env.example",
    "deploy/secure-remote/hashmm-v1300.env.example",
    "scripts/verify-server-upgrade.py"
)
$selectedEntries = @($unique | ForEach-Object {
    $_.FullName.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
})
foreach ($requiredEntry in $requiredEntries) {
    if ($selectedEntries -notcontains $requiredEntry) {
        throw "Required $release server capability missing from package selection: $requiredEntry"
    }
}

$fileManifest = @($unique | ForEach-Object {
    $relative = $_.FullName.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
    [ordered]@{
        path = $relative
        bytes = $_.Length
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
})
$packageManifest = [ordered]@{
    schema = "hashmm.server-package.v1"
    release = $release
    created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    file_count = $unique.Count
    required_capabilities = $requiredEntries
    exclusions = @(
        "frontend-next", "desktop", "installer-native", "tests", "user-data",
        "logs", "models", "indexes", "caches", "secrets", "old-archives"
    )
    files = $fileManifest
}
$packageManifestJson = $packageManifest | ConvertTo-Json -Depth 6

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$stream = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::CreateNew)
$archive = New-Object System.IO.Compression.ZipArchive(
    $stream,
    [System.IO.Compression.ZipArchiveMode]::Create,
    $false,
    [System.Text.Encoding]::UTF8
)
try {
    foreach ($file in $unique) {
        $relative = $file.FullName.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive,
            $file.FullName,
            $relative,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }

    $manifest = $archive.CreateEntry("SERVER-PACKAGE.txt", [System.IO.Compression.CompressionLevel]::Optimal)
    $writer = New-Object System.IO.StreamWriter($manifest.Open(), (New-Object System.Text.UTF8Encoding($false)))
    try {
        $writer.WriteLine("HashMM server package $release")
        $writer.WriteLine("Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
        $writer.WriteLine("Files: $($unique.Count)")
        $writer.WriteLine("Excluded: frontend-next, desktop, installer-native, Android App, tests, user data, logs, models, indexes, caches, secrets and old archives")
        $writer.WriteLine("Start: chmod +x hashmm-start.sh && ./hashmm-start.sh --doctor, then ./hashmm-start.sh")
        $writer.WriteLine("Configuration: preserve or privately upload the existing .env. .env.example is only a template and does not contain Supabase account values.")
        $writer.WriteLine("Legacy migration: place the old launcher beside start-hashmm1.sh once, run ./start-hashmm1.sh, then retain only the generated chmod-600 .env.")
    } finally {
        $writer.Dispose()
    }

    $jsonManifest = $archive.CreateEntry("SERVER-PACKAGE.json", [System.IO.Compression.CompressionLevel]::Optimal)
    $jsonWriter = New-Object System.IO.StreamWriter($jsonManifest.Open(), (New-Object System.Text.UTF8Encoding($false)))
    try {
        $jsonWriter.Write($packageManifestJson)
        $jsonWriter.Write("`n")
    } finally {
        $jsonWriter.Dispose()
    }
} finally {
    $archive.Dispose()
    $stream.Dispose()
}

$auditStream = [System.IO.File]::OpenRead($zipPath)
$auditArchive = New-Object System.IO.Compression.ZipArchive(
    $auditStream,
    [System.IO.Compression.ZipArchiveMode]::Read,
    $false,
    [System.Text.Encoding]::UTF8
)
try {
    $entryNames = @($auditArchive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    $duplicates = @($entryNames | Group-Object | Where-Object { $_.Count -gt 1 })
    if ($duplicates.Count -gt 0) {
        throw "Duplicate ZIP entry detected: $($duplicates[0].Name)"
    }
    foreach ($entryName in $entryNames) {
        if ($entryName.StartsWith("/") -or $entryName -match '(^|/)\.\.(/|$)') {
            throw "Unsafe ZIP entry detected: $entryName"
        }
        $lowerEntry = $entryName.ToLowerInvariant()
        foreach ($blocked in @("frontend-next/", "desktop/", "installer-native/", "tests/", "data/", "logs/", "models/", "indexes/")) {
            if ($lowerEntry.StartsWith($blocked)) {
                throw "Blocked directory entered server package: $entryName"
            }
        }
    }
    foreach ($requiredEntry in $requiredEntries) {
        if ($entryNames -notcontains $requiredEntry) {
            throw "Required $release server capability missing from ZIP: $requiredEntry"
        }
    }
    foreach ($manifestEntry in @("SERVER-PACKAGE.txt", "SERVER-PACKAGE.json")) {
        if ($entryNames -notcontains $manifestEntry) {
            throw "Package audit manifest missing from ZIP: $manifestEntry"
        }
    }
} finally {
    $auditArchive.Dispose()
    $auditStream.Dispose()
}

$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$shaPath = "$zipPath.sha256"
[System.IO.File]::WriteAllText($shaPath, "$hash  $zipName`n", (New-Object System.Text.UTF8Encoding($false)))

[pscustomobject]@{
    Release = $release
    Files = $unique.Count
    Entries = $unique.Count + 2
    Zip = $zipPath
    Bytes = (Get-Item -LiteralPath $zipPath).Length
    SHA256 = $hash
    Checksum = $shaPath
} | Format-List
