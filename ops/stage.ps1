# CivicCast Brief: stage a city's weekly run on a copy of its live working folder.
#
#   pwsh -File ops\stage.ps1 -Tier Dev|Test -City <city> [-Repo <checkout>] [-Port 3200]
#                            [-Days 14] [-RealVoice] [-Plan]
#
# Dev:  no upload, no deploy. The site is built on a copy and served on 127.0.0.1 only.
#       Audio is rendered with the repo's test-tone engine unless -RealVoice is given.
# Test: real voices. Changed media is uploaded under test/<city>/ in the media bucket
#       and the site is deployed to the one Pages project civiccast-brief-test, branch
#       <city>. The pages carry X-Robots-Tag: noindex and the build is refused if it
#       still points at the live city address.
#
# Every run works on a copy under C:\dev\civiccast-brief-stage\<tier>-<city>-<stamp>.
# The live working folder is only read, never written. Meeting source audio (raw/) is
# left out of the copy. The weekly run is the command the scheduled task runs.
#
# Safety, checked before any write:
#   - Pages project must be civiccast-brief-test
#   - every R2 key must start test/
#   - the copy must not be inside C:\dev\civiccast-brief-scratch
#   - no "CivicCast Brief weekly*" scheduled task may be running
#   - port 3000 is refused
#   - only the server PID this script started is ever named for stopping
#
# -Plan prints every path, command, upload target and deploy target and changes nothing.
#
# The Cloudflare credential is read exactly as ops\publish-city.ps1 reads it. It is never
# printed or logged.
#
# The Cloudflare account id comes from the user environment variable CIVICCAST_CF_ACCOUNT_ID,
# else a CLOUDFLARE_ACCOUNT_ID already set, else the first CLOUDFLARE_ACCOUNT_ID = '<32 hex>'
# line in a weekly-*.ps1 in the city folder. The id is not a secret, but only its length and
# source are printed. A Test run with no id stops before any upload.
#
# ASCII only: Windows PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI.

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Dev', 'Test')]
    [string]$Tier,

    [Parameter(Mandatory)]
    [string]$City,

    [string]$Repo = 'C:\dev\civiccast-brief',
    [string]$Python = '',
    [int]$Port = 3200,
    [int]$Days = 14,
    [switch]$RealVoice,
    [switch]$Plan,

    # Optional, for tests: the folder the copy is made in, the Pages project and the
    # R2 prefix. The defaults are the only values a real run should use.
    [string]$OutRoot = 'C:\dev\civiccast-brief-stage',
    [string]$PagesProject = 'civiccast-brief-test',
    [string]$R2Prefix = '',

    # Optional, for tests: the city working folder to copy, and a command to run in
    # place of the weekly run. The defaults are the only values a real run should use.
    [string]$Source = '',
    [string]$WeeklyCommand = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'stage-lib.ps1')

$Bucket = 'civiccast-brief-media'
$ScratchRoot = 'C:\dev\civiccast-brief-scratch'


function Say([string]$text) { Write-Host "  $text" }
function Step([string]$text) {
    Write-Host ''
    Write-Host "== $text" -ForegroundColor Cyan
}
function Stop-Stage([string]$reason) {
    Write-Host ''
    Write-Host "  STOP. $reason" -ForegroundColor Yellow
    Write-Host ''
    exit 1
}
function Show-Argv([string[]]$Argv) {
    ($Argv | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    }) -join ' '
}

function Invoke-Native {
    <# Run a native command, streaming its output to the console and a log. Returns the
       exit code. stderr is not turned into a terminating error. #>
    param(
        [Parameter(Mandatory)][scriptblock]$Script,
        [Parameter(Mandatory)][string]$LogPath
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Script 2>&1 | ForEach-Object {
            $line = "$_"
            Write-Host "    $line"
            Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Copy-Tree {
    <# robocopy a tree, leaving out the named folders at any depth. #>
    param(
        [Parameter(Mandatory)][string]$From,
        [Parameter(Mandatory)][string]$To,
        [string[]]$ExcludeDirs = @()
    )
    $argv = @($From, $To, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    foreach ($name in $ExcludeDirs) { $argv += @('/XD', $name) }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & robocopy.exe @argv 2>&1 | Out-String
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -gt 7) {
        throw "robocopy failed copying $From to $To (exit $code): $output"
    }
}

function Get-TreeFingerprint {
    <# A hash of every file's relative path, length and modified time. Read only. #>
    param([Parameter(Mandatory)][string]$Root)
    $lines = @(
        Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
            Sort-Object FullName |
            ForEach-Object {
                "$($_.FullName.Substring($Root.Length))|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)"
            }
    )
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash($bytes) } finally { $sha.Dispose() }
    $hex = ($hash | ForEach-Object { $_.ToString('x2') }) -join ''
    return [pscustomobject]@{ Hash = $hex; Count = $lines.Count }
}

function Test-PortListening {
    param([Parameter(Mandatory)][int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-HttpStatus {
    param([Parameter(Mandatory)][string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30
        return [int]$response.StatusCode
    } catch {
        return 0
    }
}


# --- checks, before anything is written -----------------------------------------------------

$City = $City.ToLowerInvariant()
if (-not $Python) { $Python = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not $R2Prefix) { $R2Prefix = "test/$City" }
$R2Prefix = $R2Prefix.TrimEnd('/') + '/'

try {
    Assert-StageTier $Tier
    Assert-StageCityName $City
    Assert-StagePort $Port
    Assert-StagePathOutsideScratch -Path $OutRoot
    Assert-StagePagesProject -Project $PagesProject
} catch {
    Stop-Stage $_.Exception.Message
}

if ($Port -eq 3000) { Stop-Stage 'Refusing port 3000: that port belongs to the live site.' }

if (-not $Source) { $Source = Join-Path $ScratchRoot (Get-StageCityFolder $City) }
try {
    Assert-StageSource -Folder $Source -City $City
} catch {
    Stop-Stage $_.Exception.Message
}

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    Stop-Stage "Refusing: no checkout at $Repo."
}

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Stage = Join-Path $OutRoot "$($Tier.ToLowerInvariant())-$City-$Stamp"
try {
    Assert-StagePathOutsideScratch -Path $Stage
    Assert-StagePathOutsideSource -Path $Stage -Source $Source
} catch {
    Stop-Stage $_.Exception.Message
}

try {
    $tasks = @(Get-ScheduledTask -TaskName 'CivicCast Brief weekly*' -ErrorAction SilentlyContinue)
    Assert-NoWeeklyTaskRunning -Tasks $tasks
} catch {
    Stop-Stage $_.Exception.Message
}

if (-not $Plan -and -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Stop-Stage "Refusing: no Python at $Python (pass -Python or create $Repo\.venv)."
}

# --- what this run will use -----------------------------------------------------------------

if ($Tier -eq 'Dev') {
    $BaseUrl = "http://127.0.0.1:$Port"
    $MediaBaseUrl = "http://127.0.0.1:$Port/media"
    $SiteDir = Join-Path $Stage 'live\site'
    $MediaDir = Join-Path $Stage 'live\media'
    $ServerLog = Join-Path $Stage 'serve.out.log'
    $ServerErr = Join-Path $Stage 'serve.err.log'
    $PidFile = Join-Path $Stage 'stage.pid'
    if ($RealVoice) {
        $AudioEngine = 'gemini'
        $FallbackEngine = 'lfm3'
        $VoiceLine = 'real voices (gemini, fallback lfm3)'
    } else {
        $AudioEngine = 'fake'
        $FallbackEngine = ''
        $VoiceLine = 'test-tone engine (fake); no voice service is called'
    }
    $DeployTarget = ''
} else {
    $BaseUrl = "https://$City.civiccast-brief-test.pages.dev"
    $MediaBaseUrl = "https://media.civiccastbrief.org/$R2Prefix".TrimEnd('/')
    $SiteDir = Join-Path $Stage 'live\site'
    $MediaDir = Join-Path $Stage 'live\media'
    $AudioEngine = 'gemini'
    $FallbackEngine = 'lfm3'
    $VoiceLine = 'real voices (gemini, fallback lfm3)'
    $DeployTarget = "Cloudflare Pages project $PagesProject, branch $City"
}

$RunArgs = @(
    '-m', 'civiccast_brief.weekly', 'run',
    '--city', "cities\$City",
    '--work', 'runs\weekly',
    '--db', 'desk.db',
    '--days', "$Days",
    '--episodes', 'live\episodes',
    '--media', 'live\media',
    '--site-out', 'live\site',
    '--base-url', $BaseUrl,
    '--media-base-url', $MediaBaseUrl,
    '--audio-engine', $AudioEngine,
    '--diarize'
)
if ($FallbackEngine) { $RunArgs += @('--audio-fallback-engine', $FallbackEngine) }

# --- plan -----------------------------------------------------------------------------------

if ($Plan) {
    Write-Host ''
    Write-Host "  CivicCast Brief stage plan (nothing will change)" -ForegroundColor Green
    Write-Host '  ------------------------------------------------------------'
    Say "tier:            $Tier"
    Say "city:            $City"
    Say "voice:           $VoiceLine"
    Say "source (read):   $Source"
    Say "copy target:     $Stage"
    Say "checkout:        $Repo"
    Say "python:          $Python"
    Say "days:            $Days"
    Say "base URL:        $BaseUrl"
    Say "media base URL:  $MediaBaseUrl"
    Say "media rewrite:   https://media.civiccastbrief.org/$City/ -> $($MediaBaseUrl.TrimEnd('/'))/ in the copy's live\episodes\*.json"
    Say ''
    Say 'copy:'
    Say "    desk.db -> $Stage\desk.db"
    Say "    cities\  -> $Stage\cities\"
    Say "    runs\    -> $Stage\runs\   (raw\ meeting audio left out)"
    Say "    live\episodes -> $Stage\live\episodes"
    Say "    live\media    -> $Stage\live\media"
    Say ''
    Say 'command (run in the copy):'
    Say "    $(Show-Argv (@($Python) + $RunArgs))"
    if ($Tier -eq 'Dev') {
        Say ''
        Say 'serve:'
        Say "    http://127.0.0.1:$Port/   (127.0.0.1 only)"
        Say "    PID written to $PidFile; stop with: Stop-Process -Id <pid>"
        Say 'upload target:  none (Dev does not upload)'
        Say 'deploy target:  none (Dev does not deploy)'
    } else {
        Say ''
        Say 'upload targets:'
        $media = @()
        if (Test-Path -LiteralPath (Join-Path $Source 'live\media')) {
            $media = @(Get-ChildItem -LiteralPath (Join-Path $Source 'live\media') -File | Sort-Object Name)
        }
        if ($media.Count -eq 0) {
            Say '    (no media files found)'
        }
        foreach ($file in $media) {
            $key = $R2Prefix + $file.Name
            Assert-StageR2Key $key
            Say "    $Bucket/$key"
        }
        Say ''
        Say 'deploy target:'
        Say "    $DeployTarget"
        Say "    $SiteDir"
        Say '    pages carry X-Robots-Tag: noindex'
    }
    Write-Host ''
    Write-Host '  PLAN ONLY: nothing was copied, run, uploaded, deployed or started.' -ForegroundColor Green
    Write-Host ''
    exit 0
}

# --- 1. copy --------------------------------------------------------------------------------

Step 'copy (the live folder is only read)'
Write-Host "  source: $Source"
Write-Host "  copy:   $Stage"

try {
    $before = Get-TreeFingerprint -Root $Source
    Say "source fingerprint before: $($before.Hash) ($($before.Count) files)"

    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Stage 'live') | Out-Null

    Copy-Item -LiteralPath (Join-Path $Source 'desk.db') -Destination (Join-Path $Stage 'desk.db')
    Copy-Tree -From (Join-Path $Source 'cities') -To (Join-Path $Stage 'cities')
    Copy-Tree -From (Join-Path $Source 'runs') -To (Join-Path $Stage 'runs') -ExcludeDirs @('raw')
    Copy-Tree -From (Join-Path $Source 'live\episodes') -To (Join-Path $Stage 'live\episodes')
    Copy-Tree -From (Join-Path $Source 'live\media') -To (Join-Path $Stage 'live\media')

    $raw = @(Get-ChildItem -LiteralPath $Stage -Recurse -Directory -Filter 'raw' -ErrorAction SilentlyContinue)
    if ($raw.Count -gt 0) {
        throw "The copy holds meeting source audio at $($raw[0].FullName); the copy must leave it out."
    }
    Say 'copy done; no raw meeting audio in it'
} catch {
    Stop-Stage "copy failed: $($_.Exception.Message)"
}

# --- 1b. stage media addresses ---------------------------------------------------------------

Step 'stage media addresses'
try {
    $rewritten = Set-StageEpisodeMediaBase -Folder (Join-Path $Stage 'live\episodes') -City $City -MediaBaseUrl $MediaBaseUrl
} catch {
    Stop-Stage "could not rewrite the copied episode media addresses: $($_.Exception.Message)"
}
Say "$rewritten episode file(s) point at $($MediaBaseUrl.TrimEnd('/'))/ instead of the live media"

if ($Tier -eq 'Dev' -and (Test-PortListening -Port $Port)) {
    Stop-Stage "Refusing: port $Port already has a listener. Stop it or choose another -Port."
}

# --- 2. the weekly run ----------------------------------------------------------------------

Step "weekly run ($VoiceLine)"
$RunLog = Join-Path $Stage 'stage-run.log'
Set-Location -LiteralPath $Stage
$env:PYTHONPATH = Join-Path $Repo 'src'
$env:PYTHONIOENCODING = 'utf-8'
$Started = Get-Date
$WeeklyExe = if ($WeeklyCommand) { $WeeklyCommand } else { $Python }
Write-Host "  $(Show-Argv (@($WeeklyExe) + $RunArgs))"
$runCode = Invoke-Native -LogPath $RunLog -Script { & $WeeklyExe @RunArgs }
Say "weekly run exit code $runCode"
$verdict = Get-StageRunVerdict -ExitCode $runCode -LogPath $RunLog

if ($Tier -eq 'Test' -and -not $verdict.Passed) {
    foreach ($problem in $verdict.Problems) { Write-Host "    $problem" -ForegroundColor Yellow }
    Stop-Stage "the weekly run exited $runCode; refusing to upload or deploy. See $RunLog."
}

$Index = Join-Path $SiteDir 'index.html'
if (-not (Test-Path -LiteralPath $Index)) {
    Stop-Stage "the weekly run (exit $runCode) did not build a site ($Index is missing). See $RunLog."
}
if ((Get-Item -LiteralPath $Index).LastWriteTime -lt $Started) {
    Stop-Stage "the site was not built in this run (exit $runCode; $Index is older than this run). See $RunLog."
}
Say 'site built in this run'

Step 'live-address guard'
$liveHits = @(Find-StageLiveAddress -Root $SiteDir -City $City)
if ($liveHits.Count -gt 0) {
    foreach ($hit in $liveHits) {
        Write-Host "    $($hit.Path): $($hit.Pattern)" -ForegroundColor Yellow
    }
    Stop-Stage "Refusing to serve, upload or deploy: the built site still points at the live address ($($liveHits.Count) file(s))."
}
Say "no .html, .xml or .json in the built site points at https://$City.civiccastbrief.org or https://media.civiccastbrief.org/$City/"

# --- 3. Dev: serve a local copy -------------------------------------------------------------

if ($Tier -eq 'Dev') {
    Step 'serve (127.0.0.1 only)'
    $siteMedia = Join-Path $SiteDir 'media'
    New-Item -ItemType Directory -Force -Path $siteMedia | Out-Null
    Copy-Item -Path (Join-Path $MediaDir '*') -Destination $siteMedia -Recurse -Force
    Say 'media copied under the served site'

    $server = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'http.server', "$Port", '--bind', '127.0.0.1', '--directory', ('"' + $SiteDir + '"')) `
        -WorkingDirectory $Stage `
        -WindowStyle Hidden `
        -RedirectStandardOutput $ServerLog `
        -RedirectStandardError $ServerErr `
        -PassThru
    Set-Content -LiteralPath $PidFile -Value "$($server.Id)" -Encoding ascii
    Say "server PID $($server.Id) (recorded in $PidFile)"

    $up = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ((Get-HttpStatus "$BaseUrl/") -eq 200) { $up = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $up) {
        Stop-Stage "the server did not answer $BaseUrl/ within 60s. PID $($server.Id) may still run; stop it with: Stop-Process -Id $($server.Id)"
    }

    Step 'verify'
    $failures = @()
    $homeStatus = Get-HttpStatus "$BaseUrl/"
    if ($homeStatus -eq 200) { Say "[ OK ] / answers 200" } else { $failures += "/ answered $homeStatus" }

    $episodeDir = Get-ChildItem -LiteralPath (Join-Path $SiteDir 'episodes') -Directory -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($episodeDir) {
        $episodeUrl = "$BaseUrl/episodes/$($episodeDir.Name)/"
        $episodeStatus = Get-HttpStatus $episodeUrl
        if ($episodeStatus -eq 200) { Say "[ OK ] $episodeUrl answers 200" }
        else { $failures += "$episodeUrl answered $episodeStatus" }
    } else {
        $failures += 'no episode page was built'
    }

    $mediaFile = Get-ChildItem -LiteralPath $siteMedia -File -Filter '*.mp3' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($mediaFile) {
        $mediaUrl = "$BaseUrl/media/$($mediaFile.Name)"
        $mediaStatus = Get-HttpStatus $mediaUrl
        if ($mediaStatus -eq 200) { Say "[ OK ] $mediaUrl answers 200" }
        else { $failures += "$mediaUrl answered $mediaStatus" }
    } else {
        $failures += 'no media file was built'
    }

    $after = Get-TreeFingerprint -Root $Source
    Say "source fingerprint after:  $($after.Hash) ($($after.Count) files)"
    if ($after.Hash -ne $before.Hash) {
        $failures += 'the live source folder changed during the run'
    } else {
        Say '[ OK ] the live source folder is unchanged (same files and modified times)'
    }

    $serverStillRunning = $null -ne (Get-Process -Id $server.Id -ErrorAction SilentlyContinue)

    Write-Host ''
    if ($failures.Count -gt 0) {
        $failures | ForEach-Object { Write-Host "  [FAIL] $_" -ForegroundColor Yellow }
        Write-Host ''
    }

    if (-not $verdict.Passed) {
        Write-Host "  NOT PASSED: weekly run exit $runCode" -ForegroundColor Yellow
        foreach ($problem in $verdict.Problems) { Write-Host "  $problem" -ForegroundColor Yellow }
        Write-Host "  run log: $RunLog"
        Write-Host "  server PID $($server.Id) is still running (site served for inspection)"
        Write-Host "  stop it with: Stop-Process -Id $($server.Id)"
        Write-Host ''
        exit 1
    }

    if ($failures.Count -gt 0) {
        Write-Host "  FAIL: Dev stage for $City (server PID $($server.Id) is still running)" -ForegroundColor Yellow
        Write-Host "  stop it with: Stop-Process -Id $($server.Id)"
        Write-Host ''
        exit 1
    }

    Write-Host "  PASS: Dev stage for $City" -ForegroundColor Green
    Write-Host "  copy:  $Stage"
    Write-Host "  site:  $BaseUrl/"
    Write-Host "  PID:   $($server.Id) (running: $serverStillRunning)"
    Write-Host "  stop:  Stop-Process -Id $($server.Id)"
    Write-Host '  upload: none   deploy: none'
    Write-Host ''
    exit 0
}

# --- 4. Test: noindex, upload, deploy -------------------------------------------------------

Step 'noindex'
$headersPath = Join-Path $SiteDir '_headers'
try {
    Set-StageNoindexHeader -Path $headersPath
} catch {
    Stop-Stage "could not add X-Robots-Tag: noindex to the built _headers file: $($_.Exception.Message)"
}
if ((Get-Content -LiteralPath $headersPath -Raw) -notmatch 'X-Robots-Tag:\s*noindex') {
    Stop-Stage 'could not add X-Robots-Tag: noindex to the built _headers file.'
}
Say 'the built site sends X-Robots-Tag: noindex on every page'

try {
    $account = Resolve-StageCloudflareAccountId `
        -UserValue ([Environment]::GetEnvironmentVariable('CIVICCAST_CF_ACCOUNT_ID', 'User')) `
        -CityFolder $Source
} catch {
    Stop-Stage $_.Exception.Message
}
# The id is an identifier, not a secret, but only its length and source are printed.
Say "Cloudflare account id: $($account.Id.Length) characters from $($account.Source)"
$env:CLOUDFLARE_ACCOUNT_ID = $account.Id
Remove-Variable account

Step 'upload changed media to R2'
Set-Location -LiteralPath (Join-Path $Repo 'worker\signup')
$token = & $Python -c "import sys; from civiccast_brief.record.credentials import read_windows_credential as r; sys.stdout.write(r('CivicCastBrief/cloudflare-deploy') or '')"
if ($token) {
    $env:CLOUDFLARE_API_TOKEN = $token
    Say 'using the saved Cloudflare deploy token'
}
Remove-Variable token

$types = @{ '.mp3' = 'audio/mpeg'; '.json' = 'application/json'; '.vtt' = 'text/vtt' }
$uploaded = 0
foreach ($file in Get-ChildItem -LiteralPath $MediaDir -File | Sort-Object Name) {
    $key = $R2Prefix + $file.Name
    try { Assert-StageR2Key $key } catch { Stop-Stage $_.Exception.Message }
    $md5 = (Get-FileHash -LiteralPath $file.FullName -Algorithm MD5).Hash.ToLowerInvariant()
    $etag = ''
    try {
        $head = Invoke-WebRequest -Method Head -Uri "https://media.civiccastbrief.org/$key" -UseBasicParsing
        $etag = ("$($head.Headers['ETag'] | Select-Object -First 1)" -replace '^W/', '').Trim('"')
    } catch { $etag = '' }
    if ($etag -eq $md5) {
        Say "unchanged $key"
        continue
    }
    $type = $types[$file.Extension]
    if (-not $type) {
        Say "skipped $($file.Name): no content type for $($file.Extension)"
        continue
    }
    # Invoke-Native runs the upload with stderr treated as ordinary output, so a wrangler
    # warning cannot become a terminating error. Success is the exit code alone.
    $uploadLog = Join-Path $Stage 'stage-upload.log'
    $outLines = [System.Collections.Generic.List[string]]::new()
    $uploadCode = Invoke-Native -LogPath $uploadLog -Script {
        & npx.cmd wrangler r2 object put "$Bucket/$key" --file $file.FullName --content-type $type --remote 2>&1 |
            ForEach-Object { $outLines.Add("$_"); $_ }
    }
    if ($uploadCode -ne 0) {
        Stop-Stage "upload failed for $($Bucket)/$key`: $($outLines | Out-String)"
    }
    Say "uploaded $Bucket/$key"
    $uploaded++
}
Say "media: $uploaded file(s) uploaded"

Step 'deploy'
try { Assert-StagePagesProject -Project $PagesProject } catch { Stop-Stage $_.Exception.Message }
Say "deploying $SiteDir to $PagesProject, branch $City"
# Invoke-Native runs the deploy with stderr treated as ordinary output, so a wrangler
# warning ("Pages now has wrangler.jsonc support") cannot become a terminating error.
# Success is the exit code alone; Invoke-Native echoes every line.
$deployLog = Join-Path $Stage 'stage-deploy.log'
$deployCode = Invoke-Native -LogPath $deployLog -Script {
    & npx.cmd wrangler pages deploy $SiteDir --project-name $PagesProject --branch $City --commit-dirty=true 2>&1
}
if ($deployCode -ne 0) {
    Stop-Stage "deploy failed (exit $deployCode)"
}

$after = Get-TreeFingerprint -Root $Source
if ($after.Hash -ne $before.Hash) {
    Stop-Stage 'the live source folder changed during the run.'
}

Write-Host ''
Write-Host "  PASS: Test stage for $City" -ForegroundColor Green
Write-Host "  copy:   $Stage"
Write-Host "  site:   $BaseUrl/"
Write-Host "  deploy: $PagesProject, branch $City"
Write-Host "  media:  $uploaded changed file(s) under $Bucket/$R2Prefix"
Write-Host '  live source folder unchanged'
Write-Host ''
exit 0
