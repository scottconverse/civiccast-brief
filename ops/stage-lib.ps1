# CivicCast Brief: the safety checks for ops\stage.ps1.
#
# Dot-source this file for the checks only. It defines functions and touches nothing:
# no folder is made, no task is read, no command is run.
#
# Every check throws with a plain reason. ops\stage.ps1 prints the reason and exits 1,
# and tests call the functions directly.
#
# ASCII only: Windows PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI.

#: The live city working folders. Nothing is ever written there by a stage run.
$script:StageScratchRoot = 'C:\dev\civiccast-brief-scratch'
#: Where a stage copy is made. Never inside the scratch root above.
$script:StageCopyRoot = 'C:\dev\civiccast-brief-stage'
#: The one Pages project a Test run may deploy to.
$script:StageTestProject = 'civiccast-brief-test'


function Get-StageCityFolder {
    <# The city's live working folder name under the scratch root.
       Longmont's is weekly-real; every other city's is weekly-<city>. #>
    param([Parameter(Mandatory)][string]$City)
    $name = $City.ToLowerInvariant()
    if ($name -eq 'longmont') { return 'weekly-real' }
    return "weekly-$name"
}


function Assert-StageCityName {
    param([Parameter(Mandatory)][string]$City)
    if ($City -notmatch '^[a-z0-9][a-z0-9-]*$') {
        throw "Refusing city '$City': use a lowercase city name such as superior or longmont."
    }
}


function Assert-StageTier {
    param([Parameter(Mandatory)][string]$Tier)
    if ($Tier -cnotin @('Dev', 'Test')) {
        throw "Refusing tier '$Tier': it must be Dev or Test."
    }
}


function Assert-StagePort {
    <# Port 3000 is the live site's port. A stage server never uses it. #>
    param([Parameter(Mandatory)][int]$Port)
    if ($Port -eq 3000) {
        throw 'Refusing port 3000: that port belongs to the live site.'
    }
    if ($Port -lt 1024 -or $Port -gt 65535) {
        throw "Refusing port $Port`: choose a port from 1024 to 65535."
    }
}


function Assert-StagePathOutsideScratch {
    <# The stage copy must never live inside the live working folders. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$ScratchRoot = 'C:\dev\civiccast-brief-scratch'
    )
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $scratch = [System.IO.Path]::GetFullPath($ScratchRoot).TrimEnd('\')
    if ($full -ieq $scratch) {
        throw "Refusing '$full': a stage copy must not be the scratch root itself."
    }
    if ($full.StartsWith($scratch + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing '$full': a stage copy must not be inside $scratch."
    }
}


function Assert-StagePathOutsideSource {
    <# The copy target must not be the source or sit inside it. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Source
    )
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $from = [System.IO.Path]::GetFullPath($Source).TrimEnd('\')
    if ($full -ieq $from) {
        throw "Refusing '$full': that is the live working folder itself."
    }
    if ($full.StartsWith($from + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing '$full': that is inside the live working folder $from."
    }
}


function Assert-StagePagesProject {
    <# Only the Test Pages project may be deployed to. #>
    param(
        [Parameter(Mandatory)][string]$Project,
        [string]$Allowed = 'civiccast-brief-test'
    )
    if ($Project -cne $Allowed) {
        throw "Refusing Pages project '$Project': only '$Allowed' may be deployed to."
    }
}


function Assert-StageR2Key {
    <# Every object a Test run uploads goes under test/ in the media bucket. #>
    param([Parameter(Mandatory)][string]$Key)
    if (-not $Key.StartsWith('test/', [System.StringComparison]::Ordinal)) {
        throw "Refusing R2 key '$Key': every test upload must start with test/."
    }
}


function Assert-NoWeeklyTaskRunning {
    <# Pass the objects Get-ScheduledTask returns, or any objects with TaskName and State. #>
    param([object[]]$Tasks = @())
    $running = @($Tasks | Where-Object { "$($_.State)" -eq 'Running' })
    if ($running.Count -gt 0) {
        $names = @($running | ForEach-Object { "$($_.TaskName)" }) -join ', '
        throw "Refusing while a weekly task is running: $names."
    }
}


function Get-StageRunVerdict {
    <# Decide whether the weekly run passed, and collect the "source problem:" lines its log
       carries. A non-zero exit code is a failed run whatever the log says.

       Returns an object: Passed, ExitCode, Problems. #>
    param(
        [Parameter(Mandatory)][int]$ExitCode,
        [Parameter(Mandatory)][string]$LogPath
    )
    $problems = @()
    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
        $problems = @(
            Get-Content -LiteralPath $LogPath |
                Where-Object { $_ -match 'source problem:' } |
                ForEach-Object { $_.Trim() }
        )
    }
    return [pscustomobject]@{
        Passed   = ($ExitCode -eq 0)
        ExitCode = $ExitCode
        Problems = $problems
    }
}


function Set-StageEpisodeMediaBase {
    <# Point the copied episode files at the tier's media base.

       A city's live live\episodes\*.json hold absolute live media addresses
       (https://media.civiccastbrief.org/<city>/<file>) in url, chapters_url and vtt_url.
       Copied into a stage run they would make every staged page play the live audio. This
       rewrites exactly that prefix, as text so the rest of each file is byte-for-byte the
       same. Meeting-video and public-record addresses are other hosts and are not touched.

       The weekly run decides what to build and what to re-render from desk.db and the run
       folders, never from these files, so the rewrite does not make it treat an episode as
       changed or render audio again.

       Returns the number of files changed. #>
    param(
        [Parameter(Mandatory)][string]$Folder,
        [Parameter(Mandatory)][string]$City,
        [Parameter(Mandatory)][string]$MediaBaseUrl
    )
    $from = "https://media.civiccastbrief.org/$City/"
    $to = $MediaBaseUrl.TrimEnd('/') + '/'
    if (-not (Test-Path -LiteralPath $Folder -PathType Container)) { return 0 }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $changed = 0
    foreach ($file in Get-ChildItem -LiteralPath $Folder -File -Filter '*.json' | Sort-Object Name) {
        $text = [System.IO.File]::ReadAllText($file.FullName)
        if (-not $text.Contains($from)) { continue }
        $new = $text.Replace($from, $to)
        [System.IO.File]::WriteAllText($file.FullName, $new, $utf8)
        $changed++
    }
    return $changed
}


function Find-StageLiveAddress {
    <# Every built .html, .xml and .json that still names the live city site or the live city
       media prefix. Returns one object per hit (Path, Pattern); an empty list means clear. #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$City
    )
    $patterns = @(
        "https://$City.civiccastbrief.org",
        "https://media.civiccastbrief.org/$City/"
    )
    $hits = @()
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $hits }
    foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue) {
        if ($file.Extension -notin @('.html', '.xml', '.json')) { continue }
        $text = [System.IO.File]::ReadAllText($file.FullName)
        foreach ($pattern in $patterns) {
            if ($text.Contains($pattern)) {
                $hits += [pscustomobject]@{ Path = $file.FullName; Pattern = $pattern }
            }
        }
    }
    return $hits
}


function Set-StageNoindexHeader {
    <# Put X-Robots-Tag: noindex inside the built site's /* rule, so it covers every page.
       Creates the /* rule when it is missing. Other rules are left as they are. #>
    param([Parameter(Mandatory)][string]$Path)
    $header = '  X-Robots-Tag: noindex'
    $text = ''
    if (Test-Path -LiteralPath $Path) { $text = [System.IO.File]::ReadAllText($Path) }
    $nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in [regex]::Split($text, "\r?\n")) { $lines.Add($line) }

    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^/\*\s*$') { $start = $i; break }
    }
    if ($start -lt 0) {
        $lines.Insert(0, '')
        $lines.Insert(0, $header)
        $lines.Insert(0, '/*')
    } else {
        $end = $start + 1
        while ($end -lt $lines.Count -and $lines[$end] -match '^\s+\S') { $end++ }
        $block = @($lines.GetRange($start + 1, $end - $start - 1))
        $has = @($block | Where-Object { $_ -match '^\s*X-Robots-Tag:\s*noindex' }).Count -gt 0
        if (-not $has) { $lines.Insert($start + 1, $header) }
    }
    $out = ($lines -join $nl)
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $out, $utf8)
}


function Assert-StageSource {
    <# The live working folder must be a real city folder before anything is copied. #>
    param(
        [Parameter(Mandatory)][string]$Folder,
        [Parameter(Mandatory)][string]$City
    )
    if (-not (Test-Path -LiteralPath $Folder -PathType Container)) {
        throw "Refusing: no city working folder at $Folder."
    }
    foreach ($needed in @('desk.db', "cities\$City", 'runs', 'live\episodes', 'live\media')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Folder $needed))) {
            throw "Refusing: $Folder has no $needed."
        }
    }
}
