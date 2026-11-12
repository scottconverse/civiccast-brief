# Install CivicCast Brief on a Windows machine, in one step.
#
# From a copy of the code:   double-click install.cmd, or run  powershell -ExecutionPolicy Bypass -File install.ps1
# With no copy yet:          powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/scottconverse/civiccast-brief/main/install.ps1 | iex"
#
# What it does, in order:
#   1. Gets the code (when run without a copy) into %LOCALAPPDATA%\CivicCastBrief\app.
#   2. Installs uv when it is missing (Astral's own installer, for this user only).
#   3. Installs the Python packages with uv; uv brings Python 3.12 itself when the machine has none.
#   4. Installs ffmpeg when it is missing (winget when there is one, else the gyan.dev build,
#      checked against its published SHA-256), for this user only.
#   5. Installs yt-dlp into CivicCast Brief's own folder (python -m civiccast_brief tools).
#   6. Checks for Chrome and for Ollama with the writer model, and says what is missing.
#   7. Writes desk.cmd, which starts the editor desk. Setting up a city happens in the desk
#      (Sources > Find a city's sources) or with  python -m civiccast_brief city find.
#
# TownRecord and the speech code are part of CivicCast Brief; nothing else is installed for them.
# The voice engine runs on the GPU machine and is not set up here.
# Nothing needs administrator rights. Runs on Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    # Where the code goes when the script is run without a copy of it.
    [string]$AppDir = (Join-Path $env:LOCALAPPDATA 'CivicCastBrief\app'),
    # The branch or tag to download when there is no copy.
    [string]$Ref = 'main',
    # A public YouTube meeting video with captions. A new yt-dlp is kept only when it reads these captions.
    [string]$TestVideo = 'https://www.youtube.com/watch?v=Fcdk3MPz8Ic',
    # Also install speaker diarization (large: PyTorch and pyannote).
    [switch]$Diarize,
    # Print the steps and change nothing.
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is many times slower with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repo = 'scottconverse/civiccast-brief'
$WriterModel = 'deepseek-v4.1-flash:1017-cloud'
$FfmpegZip = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$ToolsDir = Join-Path $env:LOCALAPPDATA 'CivicCastBrief'
$script:Missing = @()

function Step([string]$text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok([string]$text) { Write-Host "   ok: $text" -ForegroundColor Green }
function Note([string]$text) { Write-Host "   $text" }
function Warn([string]$text) { Write-Host "   missing: $text" -ForegroundColor Yellow; $script:Missing += $text }

function Have([string]$name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# PATH as the user and machine have it now, so a tool installed a moment ago is found.
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user, $env:Path) | Where-Object { $_ }) -join ';'
}

function Add-UserPath([string]$dir) {
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($user -split ';' | Where-Object { $_ })
    if ($parts -notcontains $dir) {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $dir) -join ';'), 'User')
    }
    if (($env:Path -split ';') -notcontains $dir) { $env:Path = "$env:Path;$dir" }
}

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what failed with exit code $LASTEXITCODE" }
}

$steps = @(
    'Get the code',
    'uv',
    'Python packages',
    'ffmpeg',
    'yt-dlp',
    'Chrome and Ollama',
    'desk.cmd'
)
if ($PlanOnly) {
    $i = 1
    foreach ($s in $steps) { Write-Output "$i. $s"; $i++ }
    return
}

# 1. The code. A copy next to this script is used as it is.
Step $steps[0]
$here = $PSScriptRoot
if ($here -and (Test-Path (Join-Path $here 'pyproject.toml'))) {
    $AppDir = $here
    Ok "using the copy in $AppDir"
} else {
    $zip = Join-Path $env:TEMP "civiccast-brief-$Ref.zip"
    $unpack = Join-Path $env:TEMP "civiccast-brief-$Ref"
    Note "downloading $Repo ($Ref)"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$Repo/archive/$Ref.zip" -OutFile $zip
    if (Test-Path $unpack) { Remove-Item -Recurse -Force $unpack }
    Expand-Archive -Path $zip -DestinationPath $unpack
    $top = Get-ChildItem $unpack -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force $AppDir | Out-Null
    # Only an empty folder, or an earlier install, is cleared. Anything else is left alone.
    $earlier = Join-Path $AppDir 'pyproject.toml'
    $isEarlier = (Test-Path $earlier) -and ((Get-Content $earlier -Raw) -match 'name = "civiccast-brief"')
    if (-not $isEarlier -and (Get-ChildItem $AppDir -Force | Where-Object { $_.Name -ne '.venv' })) {
        throw "$AppDir already holds other files; pick an empty folder with -AppDir"
    }
    # The code is replaced; the Python environment (.venv) is kept so a reinstall is quick.
    Get-ChildItem $AppDir -Force | Where-Object { $_.Name -ne '.venv' } | Remove-Item -Recurse -Force
    Get-ChildItem $top.FullName -Force | Move-Item -Destination $AppDir
    Remove-Item -Recurse -Force $unpack, $zip
    Ok "code in $AppDir"
}

# 2. uv
Step $steps[1]
Update-SessionPath
if (-not (Have 'uv')) {
    Note "installing uv for this user (https://astral.sh/uv)"
    # Astral's installer runs in its own PowerShell, so an exit inside it cannot end this script.
    $uvSetup = Join-Path $env:TEMP 'uv-install.ps1'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $uvSetup
    Invoke-Checked 'the uv installer' { powershell -NoProfile -ExecutionPolicy Bypass -File $uvSetup }
    Remove-Item $uvSetup
    Update-SessionPath
    Add-UserPath (Join-Path $env:USERPROFILE '.local\bin')
}
if (-not (Have 'uv')) { throw 'uv is still not on PATH after installing it' }
Ok "uv $((uv --version) -replace '^uv ', '')"

# 3. Python packages. uv downloads Python 3.12 when the machine has none.
Step $steps[2]
Push-Location $AppDir
try {
    $sync = @('sync', '--frozen', '--python', '3.12', '--extra', 'browser')
    if ($Diarize) { $sync += @('--extra', 'diarize') }
    Invoke-Checked 'uv sync' { uv @sync }
} finally { Pop-Location }
$python = Join-Path $AppDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "uv sync made no Python at $python" }
Ok "$(& $python --version) in $AppDir\.venv"

# 4. ffmpeg and ffprobe
Step $steps[3]
if (-not ((Have 'ffmpeg') -and (Have 'ffprobe'))) {
    $installed = $false
    if (Have 'winget') {
        Note 'installing ffmpeg with winget, for this user'
        winget install --id Gyan.FFmpeg -e --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
        Update-SessionPath
        $installed = (Have 'ffmpeg') -and (Have 'ffprobe')
    }
    if (-not $installed) {
        Note "downloading ffmpeg from $FfmpegZip"
        $zip = Join-Path $env:TEMP 'ffmpeg-release-essentials.zip'
        Invoke-WebRequest -UseBasicParsing -Uri $FfmpegZip -OutFile $zip
        $want = ((Invoke-WebRequest -UseBasicParsing -Uri "$FfmpegZip.sha256").Content | Out-String).Trim().Split()[0].ToLower()
        $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $want) { Remove-Item $zip; throw "the ffmpeg download does not match its published SHA-256 ($got, expected $want)" }
        $dest = Join-Path $ToolsDir 'ffmpeg'
        if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
        Expand-Archive -Path $zip -DestinationPath $dest
        Remove-Item $zip
        $bin = (Get-ChildItem $dest -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName
        Add-UserPath $bin
    }
}
if (-not ((Have 'ffmpeg') -and (Have 'ffprobe'))) { throw 'ffmpeg or ffprobe is still not on PATH' }
Ok ((ffmpeg -hide_banner -version | Select-Object -First 1) -replace ' Copyright.*', '')

# 5. yt-dlp, in CivicCast Brief's own folder. It is kept only when it reads the test video's captions.
Step $steps[4]
$env:PYTHONPATH = ''
$env:PYTHONIOENCODING = 'utf-8'
& $python -m civiccast_brief tools --test-video $TestVideo
if ($LASTEXITCODE -eq 0) { Ok 'yt-dlp is ready' }
else { Warn "yt-dlp: it did not read the test video's captions (exit code $LASTEXITCODE). Run  `"$python`" -m civiccast_brief tools --test-video <URL>  later. Granicus cities do not need it." }

# 6. Chrome and Ollama are separate programs. They are checked, not installed.
Step $steps[5]
$chrome = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($chrome) { Ok "Chrome at $chrome" }
else { Warn 'Chrome. A few meeting sites can only be read through it. Install it from https://www.google.com/chrome/' }
try {
    $tags = Invoke-RestMethod -UseBasicParsing -TimeoutSec 5 -Uri 'http://127.0.0.1:11434/api/tags'
    if (@($tags.models | ForEach-Object { $_.name }) -contains $WriterModel) { Ok "Ollama with $WriterModel" }
    else { Warn "the writer model. Ollama is running but does not list $WriterModel. Run  ollama pull $WriterModel" }
} catch {
    Warn "Ollama at 127.0.0.1:11434. The recap and script writers need it. Install it from https://ollama.com/download, then run  ollama pull $WriterModel"
}

# 7. A starter for the editor desk. Its data lives in Documents\CivicCast Brief, not with the code.
Step $steps[6]
$launcher = Join-Path $AppDir 'desk.cmd'
$lines = @(
    '@echo off',
    'rem Starts the CivicCast Brief editor desk at http://127.0.0.1:8765/ . Written by install.ps1.',
    'set "WORK=%USERPROFILE%\Documents\CivicCast Brief"',
    'if not exist "%WORK%" mkdir "%WORK%"',
    'set PYTHONIOENCODING=utf-8',
    'start "" http://127.0.0.1:8765/',
    "`"$python`" -m civiccast_brief.review serve --db `"%WORK%\desk.db`""
)
Set-Content -Path $launcher -Value $lines -Encoding ascii
Ok "wrote $launcher"

Write-Host ""
if ($script:Missing.Count -eq 0) {
    Write-Host 'CivicCast Brief is installed.' -ForegroundColor Green
} else {
    Write-Host "CivicCast Brief is installed. $($script:Missing.Count) thing(s) still to do:" -ForegroundColor Yellow
    foreach ($m in $script:Missing) { Write-Host " - $m" }
}
Write-Host "Start the editor desk:  `"$launcher`""
Write-Host "Set up a city: in the desk, Sources > Find a city's sources. Or:"
Write-Host "  `"$python`" -m civiccast_brief city find --name `"Your City`" --state CO --dry-run"
