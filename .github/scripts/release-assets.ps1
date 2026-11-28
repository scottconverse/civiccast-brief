# Builds the Windows install files for one CivicCast Brief release.
#
# There is no .exe. The installer is install.cmd plus install.ps1. A copy of install.ps1 with
# the default Ref changed to the release tag installs that exact version, so this script pins
# the copy and puts it, install.cmd, a zip of the two, and SHA256SUMS.txt in -OutDir.
#
# Runs on Windows PowerShell 5.1 and PowerShell 7. It changes nothing outside -OutDir.

[CmdletBinding()]
param(
    # The release tag to pin, for example v0.6.0.
    [Parameter(Mandatory = $true)]
    [string]$Tag,
    # Where the files are written. Made when it does not exist; nothing else there is touched.
    [Parameter(Mandatory = $true)]
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Tag)) { throw 'Tag is empty' }
if ($Tag -match '[\r\n]') { throw 'Tag has a line break' }

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$sourcePs1 = Join-Path $repoRoot 'install.ps1'
$sourceCmd = Join-Path $repoRoot 'install.cmd'
foreach ($path in @($sourcePs1, $sourceCmd)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "missing $path" }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$outDir = (Resolve-Path -LiteralPath $OutDir).Path
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# install.ps1, with only its default Ref line changed, so a run without a copy fetches the tag.
$original = [System.IO.File]::ReadAllText($sourcePs1)
$from = "[string]`$Ref = 'main',"
$to = "[string]`$Ref = '$Tag',"
$found = ([regex]::Matches($original, [regex]::Escape($from))).Count
if ($found -ne 1) { throw "expected exactly one '$from' in install.ps1, found $found" }
$pinnedPath = Join-Path $outDir 'install.ps1'
[System.IO.File]::WriteAllText($pinnedPath, $original.Replace($from, $to), $utf8NoBom)

# install.cmd, byte for byte.
$cmdPath = Join-Path $outDir 'install.cmd'
[System.IO.File]::WriteAllBytes($cmdPath, [System.IO.File]::ReadAllBytes($sourceCmd))

# The zip, with the two files at its root and no folder above them.
foreach ($assembly in @('System.IO.Compression', 'System.IO.Compression.FileSystem')) {
    try { Add-Type -AssemblyName $assembly -ErrorAction Stop } catch { }
}
if (-not ('System.IO.Compression.ZipFile' -as [type])) {
    throw 'System.IO.Compression.ZipFile is not available'
}
$zipName = "civiccast-brief-install-$Tag.zip"
$zipPath = Join-Path $outDir $zipName
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in @($pinnedPath, $cmdPath)) {
        $entry = [System.IO.Path]::GetFileName($file)
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $file, $entry, [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
} finally { $zip.Dispose() }

# SHA-256 through .NET, so the script does not depend on a module being auto-loaded.
function Get-Sha256([string]$path) {
    $stream = [System.IO.File]::OpenRead($path)
    try { $bytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash($stream) }
    finally { $stream.Dispose() }
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLower()
}

# One checksum line per file written above, by name.
$sums = @($pinnedPath, $cmdPath, $zipPath) |
    ForEach-Object { Get-Item -LiteralPath $_ } |
    Sort-Object -Property Name |
    ForEach-Object { "$(Get-Sha256 $_.FullName)  $($_.Name)" }
$sumsPath = Join-Path $outDir 'SHA256SUMS.txt'
[System.IO.File]::WriteAllText($sumsPath, (($sums -join "`n") + "`n"), $utf8NoBom)

Write-Output "wrote:"
foreach ($path in @($pinnedPath, $cmdPath, $zipPath, $sumsPath)) { Write-Output "  $path" }
