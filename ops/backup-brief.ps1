# backup-brief.ps1
# Nightly backup of the CivicCast Brief working files to drive D:.
# Run by the coordinator; this script does not schedule itself.

[CmdletBinding()]
param(
    [string]$Dest   = 'D:\CivicCast-Brief-backups',
    [string]$Source = 'C:\dev\civiccast-brief-scratch',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Python     = 'C:\dev\civiccast-brief\.venv\Scripts\python.exe'
$KeepDaily  = 30          # newest dated folders to keep
$MonthlyMax = 366         # keep one folder per month if younger than this

$stamp    = Get-Date
$runDate  = $stamp.ToString('yyyy-MM-dd')
$dailyDir = Join-Path $Dest 'daily'
$runDir   = Join-Path $dailyDir $runDate
$logFile  = Join-Path $Dest 'backup.log'
$lastFile = Join-Path $Dest 'last-result.txt'

# Collect log lines in memory, write once at the end.
$log = [System.Collections.Generic.List[string]]::new()
function Write-Log([string]$msg) {
    $log.Add($msg)
    Write-Host $msg
}

# Decide which dated folder names in a daily folder to keep.
#   - the newest $KeepDaily dated folders
#   - for each calendar month (year + month), the OLDEST dated folder of that
#     month, if it is less than $MonthlyMax days old.
# The backup only runs when the PC is on and the user is logged in, so a
# "Day -eq 1" rule would keep no monthly copy in a month with no run on the 1st.
# Both the -DryRun preview and the real run use this one function.
function Get-RetentionKeep([string]$dir, [datetime]$now) {
    $dated = @(Get-ChildItem -LiteralPath $dir -Directory |
               Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' })
    $parsed = foreach ($d in $dated) {
        $dt = [datetime]::MinValue
        if ([datetime]::TryParseExact($d.Name, 'yyyy-MM-dd',
                [System.Globalization.CultureInfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::None, [ref]$dt)) {
            [pscustomobject]@{ Name = $d.Name; Date = $dt }
        }
    }
    $sorted = @($parsed | Sort-Object Date -Descending)
    $keep = [System.Collections.Generic.HashSet[string]]::new()
    for ($i = 0; $i -lt [math]::Min($KeepDaily, $sorted.Count); $i++) { [void]$keep.Add($sorted[$i].Name) }
    $monthly = $parsed |
        Group-Object { '{0}-{1:D2}' -f $_.Date.Year, $_.Date.Month } |
        ForEach-Object { $_.Group | Sort-Object Date | Select-Object -First 1 }
    foreach ($p in $monthly) {
        if (($now.Date - $p.Date).Days -lt $MonthlyMax) { [void]$keep.Add($p.Name) }
    }
    return [pscustomobject]@{ Sorted = $sorted; Keep = $keep }
}

function Get-FolderMb([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return 0.0 }
    $bytes = (Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { return 0.0 }
    return [math]::Round($bytes / 1MB, 2)
}

# robocopy returns 0-7 for success. Return the code without treating it as an error.
function Invoke-Robocopy([string]$src, [string]$dst, [string[]]$extra) {
    $old = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        & robocopy $src $dst /E /XF *.wav desk*.db desk*.db-wal desk*.db-shm desk*.db-journal `
            /R:2 /W:5 /NP /NFL /NDL @extra | Out-Null
        return $LASTEXITCODE
    }
    finally {
        $PSNativeCommandUseErrorActionPreference = $old
    }
}

# Run the venv python and return its exit code.
function Invoke-Python([string]$code, [string[]]$pyArgs) {
    $old = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        & $Python -I -c $code @pyArgs
        return $LASTEXITCODE
    }
    finally {
        $PSNativeCommandUseErrorActionPreference = $old
    }
}

# Copy one SQLite database safely with the SQLite backup API.
# A zero-byte file is copied as-is.
function Copy-SqliteDb([string]$srcFile, [string]$dstFile) {
    if ((Get-Item -LiteralPath $srcFile).Length -eq 0) {
        Copy-Item -LiteralPath $srcFile -Destination $dstFile -Force
        return
    }
    # File URI needs forward slashes on Windows.
    $uriSrc = $srcFile.Replace('\', '/')
    # If the app has the database open in WAL mode its sidecar files exist.
    # Read normally then (the app is already using them). Otherwise read with
    # immutable=1 so SQLite never creates anything in the source folder.
    $live = (Test-Path -LiteralPath "$srcFile-wal") -or (Test-Path -LiteralPath "$srcFile-shm")
    $uri = if ($live) { "file:${uriSrc}?mode=ro" } else { "file:${uriSrc}?mode=ro&immutable=1" }
    $code = "import sqlite3,sys`ntry:`n s=sqlite3.connect(sys.argv[2], uri=True); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close(); s.close()`nexcept Exception as e:`n sys.stderr.write(str(e)+chr(10)); sys.exit(1)"
    $rc = Invoke-Python $code @($dstFile, $uri)
    if ($rc -ne 0) { throw "sqlite backup failed for $srcFile (exit $rc)" }
}

# Integrity check the copy. Returns $true when the result is "ok".
# The copy is not being written, so immutable=1 is safe and creates no sidecars.
function Test-SqliteIntegrity([string]$file) {
    if ((Get-Item -LiteralPath $file).Length -eq 0) { return $true }  # empty db is valid
    $uri = "file:" + $file.Replace('\', '/') + '?mode=ro&immutable=1'
    $code = "import sqlite3,sys; c=sqlite3.connect(sys.argv[1], uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0])"
    $out = & $Python -I -c $code $uri 2>&1
    return (($LASTEXITCODE -eq 0) -and (($out | Out-String).Trim() -eq 'ok'))
}

# ---------------------------------------------------------------- start

Write-Log ("=== Backup start {0} ===" -f $stamp.ToString('yyyy-MM-dd HH:mm'))

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    $msg = "Source folder not found: $Source"
    Write-Log "FAIL $msg"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        Set-Content -LiteralPath $lastFile -Value ("FAIL {0} {1}" -f $stamp.ToString('yyyy-MM-dd HH:mm'), $msg) -ErrorAction SilentlyContinue
        Add-Content -LiteralPath $logFile  -Value $log -ErrorAction SilentlyContinue
    }
    exit 1
}

$cities = @(Get-ChildItem -LiteralPath $Source -Directory -Filter 'weekly-*' |
            Sort-Object Name)
$publishScript = Join-Path $Source 'publish-city.ps1'

if ($DryRun) {
    Write-Host "DRY RUN - nothing will change"
    Write-Host "Source : $Source"
    Write-Host "Dest   : $Dest"
    Write-Host "Run dir: $runDir"
    Write-Host "Cities : $($cities.Count)"
    foreach ($c in $cities) {
        Write-Host "  would robocopy $($c.FullName) -> $(Join-Path $runDir $c.Name)"
        $dbs = @(Get-ChildItem -LiteralPath $c.FullName -File -Filter 'desk*.db')
        foreach ($db in $dbs) {
            Write-Host "  would sqlite-backup $($db.Name) ($([math]::Round($db.Length/1MB,2)) MB)"
        }
    }
    if (Test-Path -LiteralPath $publishScript) {
        Write-Host "  would copy publish-city.ps1"
    }
    Write-Host "  would replace contents of $runDir"

    # Show what retention would delete (based on what is on disk today).
    if (Test-Path -LiteralPath $dailyDir) {
        $retention = Get-RetentionKeep $dailyDir $stamp
        $sorted = @($retention.Sorted)
        $keep = $retention.Keep
        $wouldDelete = @($sorted | Where-Object { -not $keep.Contains($_.Name) })
        Write-Host ("  retention would delete {0} folder(s): {1}" -f $wouldDelete.Count,
                    (($wouldDelete | ForEach-Object Name) -join ', '))
    }
    Write-Host "DRY RUN complete - no changes made"
    exit 0
}

# ---------------------------------------------------------------- run

try {
    New-Item -ItemType Directory -Force -Path $dailyDir | Out-Null
    if (Test-Path -LiteralPath $runDir) {
        Remove-Item -LiteralPath $runDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
}
catch {
    $reason = "cannot prepare $runDir : $($_.Exception.Message)"
    Write-Log "FAIL $reason"
    Set-Content -LiteralPath $lastFile -Value ("FAIL {0} {1}" -f $stamp.ToString('yyyy-MM-dd HH:mm'), $reason) -ErrorAction SilentlyContinue
    Add-Content -LiteralPath $logFile  -Value $log -ErrorAction SilentlyContinue
    exit 1
}

$failed = [System.Collections.Generic.List[string]]::new()

foreach ($city in $cities) {
    $dstCity = Join-Path $runDir $city.Name
    try {
        $rc = Invoke-Robocopy $city.FullName $dstCity @()
        if ($rc -ge 8) { throw "robocopy exit $rc" }

        # SQLite databases copied safely with the backup API.
        $dbs = @(Get-ChildItem -LiteralPath $city.FullName -File -Filter 'desk*.db')
        foreach ($db in $dbs) {
            $dstDb = Join-Path $dstCity $db.Name
            Copy-SqliteDb $db.FullName $dstDb
            if (-not (Test-SqliteIntegrity $dstDb)) {
                throw "integrity_check failed on $($db.Name)"
            }
        }

        $mb = Get-FolderMb $dstCity
        Write-Log ("{0} OK {1} MB" -f $city.Name, $mb)
    }
    catch {
        $failed.Add($city.Name)
        Write-Log ("{0} FAIL {1}" -f $city.Name, $_.Exception.Message)
    }
}

# publish-city.ps1 sits at the source root, not inside a city folder.
if (Test-Path -LiteralPath $publishScript -PathType Leaf) {
    try {
        Copy-Item -LiteralPath $publishScript -Destination (Join-Path $runDir 'publish-city.ps1') -Force
    }
    catch {
        $failed.Add('publish-city.ps1')
        Write-Log ("publish-city.ps1 FAIL {0}" -f $_.Exception.Message)
    }
}

$totalMb = Get-FolderMb $runDir
Write-Log ("TOTAL {0} MB" -f $totalMb)

# ---------------------------------------------------------------- retention

$deleted = @()
if ($failed.Count -eq 0) {
    $retention = Get-RetentionKeep $dailyDir $stamp
    $sorted = @($retention.Sorted)
    $keep = $retention.Keep
    foreach ($p in $sorted) {
        if ($keep.Contains($p.Name)) { continue }
        $fullName = Join-Path $dailyDir $p.Name
        # Only exact date-named folders, only under daily\.
        if (-not (Test-Path -LiteralPath $fullName -PathType Container)) { continue }
        try {
            Remove-Item -LiteralPath $fullName -Recurse -Force
            $deleted += $p.Name
        }
        catch {
            Write-Log ("Retention FAIL deleting {0}: {1}" -f $p.Name, $_.Exception.Message)
        }
    }
}
else {
    Write-Log "Retention skipped because the run failed"
}

if ($deleted.Count -gt 0) {
    Write-Log ("Retention deleted: {0}" -f ($deleted -join ', '))
}
else {
    Write-Log "Retention deleted: none"
}

# ---------------------------------------------------------------- finish

$finish = Get-Date
$overall = if ($failed.Count -eq 0) { 'OK' } else { 'FAIL' }
Write-Log ("=== Backup finish {0} : {1} ===" -f $finish.ToString('yyyy-MM-dd HH:mm'), $overall)

Add-Content -LiteralPath $logFile -Value $log

if ($overall -eq 'OK') {
    $line = "OK {0} {1}" -f $finish.ToString('yyyy-MM-dd HH:mm'), $totalMb
}
else {
    $line = "FAIL {0} {1}" -f $finish.ToString('yyyy-MM-dd HH:mm'), ($failed -join ', ')
}
Set-Content -LiteralPath $lastFile -Value $line

if ($overall -eq 'OK') { exit 0 } else { exit 1 }
