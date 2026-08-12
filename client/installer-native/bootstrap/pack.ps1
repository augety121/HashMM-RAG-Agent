# pack.ps1 - append a folder's contents into the bootstrap stub -> single self-extracting exe.
# Usage: powershell -ExecutionPolicy Bypass -File pack.ps1 -Folder payload -Stub bootstrap.exe -Out HashMM-Setup.exe
param(
  [Parameter(Mandatory=$true)][string]$Folder,
  [Parameter(Mandatory=$true)][string]$Stub,
  [Parameter(Mandatory=$true)][string]$Out
)
$ErrorActionPreference = "Stop"

$folderFull = (Resolve-Path -LiteralPath $Folder).Path.TrimEnd('\')
$files = Get-ChildItem -LiteralPath $folderFull -Recurse -File
if ($files.Count -eq 0) { throw "Payload folder is empty: $folderFull" }

$fsOut = [System.IO.File]::Create($Out)
try {
    # 1) write the stub (bootstrap) bytes; remember where payload begins
    $stubBytes = [System.IO.File]::ReadAllBytes($Stub)
    $fsOut.Write($stubBytes, 0, $stubBytes.Length)
    [int64]$payloadOffset = $stubBytes.Length

    $enc = [System.Text.Encoding]::UTF8
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($folderFull.Length).TrimStart('\','/')
        $pathBytes = $enc.GetBytes($rel)
        # [u32 pathLen][path][i64 dataLen][data]
        $fsOut.Write([System.BitConverter]::GetBytes([uint32]$pathBytes.Length), 0, 4)
        $fsOut.Write($pathBytes, 0, $pathBytes.Length)
        $fsOut.Write([System.BitConverter]::GetBytes([int64]$f.Length), 0, 8)
        $fin = [System.IO.File]::OpenRead($f.FullName)
        try { $fin.CopyTo($fsOut) } finally { $fin.Close() }
    }
    # sentinel: 0xFFFFFFFF
    $fsOut.Write([System.BitConverter]::GetBytes([uint32]4294967295), 0, 4)
    # footer: [i64 payloadOffset][magic "HMSFX1\0\0"]
    $fsOut.Write([System.BitConverter]::GetBytes([int64]$payloadOffset), 0, 8)
    $magic = [byte[]](0x48,0x4D,0x53,0x46,0x58,0x31,0x00,0x00)
    $fsOut.Write($magic, 0, 8)
} finally {
    $fsOut.Close()
}
Write-Host ("[OK] Packed {0} files -> {1}" -f $files.Count, $Out)
