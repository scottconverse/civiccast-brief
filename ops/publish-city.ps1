# CivicCast Brief: publish only (no new meetings): build the site from the desk, upload media, deploy.
# Usage: pwsh -File publish-city.ps1 -City longmont -Here C:\dev\civiccast-brief-scratch\weekly-real
param([Parameter(Mandatory)][string]$City, [Parameter(Mandatory)][string]$Here, [string]$Repo = 'C:\dev\civiccast-brief')
#
# 1. weekly publish: every published episode gets its audio and episode file; the site is
#    built into live\site. No new meetings are fetched.
# 2. Media files that are new or changed are uploaded to R2 (bucket civiccast-brief-media,
#    served at media.civiccastbrief.org/$City). The served ETag is the file's MD5.
# 3. When the site was built in this run, it is deployed to Pages (civiccast-brief-<city>).
#
# Nothing reaches the site unless it is published on the desk.
# The log of each run is in live\logs.

# Exit codes are checked by hand; a line a tool writes to stderr is not a failure.
$ErrorActionPreference = 'Continue'

# $Repo is a parameter (default: the main checkout).
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Bucket = 'civiccast-brief-media'
$Prefix = "$City/"
$Project = "civiccast-brief-$City"

$env:PYTHONPATH = Join-Path $Repo 'src'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $Here
$stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
$log = Join-Path $Here "live\logs\publish-$stamp.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Say([string]$text) { $line = "$(Get-Date -Format 's') $text"; Add-Content -Path $log -Value $line -Encoding utf8; Write-Output $line }

$started = Get-Date
$site = Join-Path $Here 'live\site'
Say "publish started"
& $Python -m civiccast_brief.weekly publish --city "cities\$City" --work runs\weekly --db desk.db `
    --episodes live\episodes --media live\media --site-out $site `
    --base-url https://$City.civiccastbrief.org --media-base-url https://media.civiccastbrief.org/$City --audio-engine dia2 `
    *>&1 | ForEach-Object { Add-Content -Path $log -Value "$_" -Encoding utf8 }
$runCode = $LASTEXITCODE
Say "publish exit code $runCode"

$index = Join-Path $site 'index.html'
if (-not (Test-Path $index) -or (Get-Item $index).LastWriteTime -lt $started) {
    Say "site not built in this run; nothing deployed"
    exit $runCode
}

# Upload the media files R2 does not hold yet, or holds with other bytes. The public address
# answers with the object's MD5 as its ETag. Wrangler is used (not the cf CLI) because the
# scheduler runs outside the Claude app, where only wrangler's sign-in is stored.
Set-Location (Join-Path $Repo 'worker\signup')
# A Cloudflare API token saved in Credential Manager (target CivicCastBrief/cloudflare-deploy)
# signs wrangler in. Without one, wrangler uses its own sign-in (npx wrangler login, run once
# in a terminal outside the Claude app). The token is never printed or logged.
$token = & $Python -c "import sys; from civiccast_brief.record.credentials import read_windows_credential as r; sys.stdout.write(r('CivicCastBrief/cloudflare-deploy') or '')"
if ($token) { $env:CLOUDFLARE_API_TOKEN = $token; Say "using the saved Cloudflare deploy token" }
# The token is an account token, so wrangler is told the account and does not need to list accounts.
# The account ID is read from the user environment variable CIVICCAST_CF_ACCOUNT_ID.
$env:CLOUDFLARE_ACCOUNT_ID = [Environment]::GetEnvironmentVariable('CIVICCAST_CF_ACCOUNT_ID', 'User')
Remove-Variable token
$types = @{ '.mp3' = 'audio/mpeg'; '.json' = 'application/json'; '.vtt' = 'text/vtt' }
$uploaded = 0
foreach ($f in Get-ChildItem (Join-Path $Here 'live\media') -File) {
    $key = $Prefix + $f.Name
    $md5 = (Get-FileHash $f.FullName -Algorithm MD5).Hash.ToLower()
    $etag = ''
    try {
        $head = Invoke-WebRequest -Method Head -Uri "https://media.civiccastbrief.org/$key" -UseBasicParsing
        # A compressed answer (JSON, VTT) marks the same ETag weak: W/"<md5>".
        $etag = ("$($head.Headers['ETag'] | Select-Object -First 1)" -replace '^W/', '').Trim('"')
    } catch { $etag = '' }
    if ($etag -eq $md5) { continue }
    $type = $types[$f.Extension]
    if (-not $type) { Say "skipped $($f.Name): no content type for $($f.Extension)"; continue }
    $out = & npx.cmd wrangler r2 object put "$Bucket/$key" --file $f.FullName --content-type $type --remote 2>&1
    if ($LASTEXITCODE -ne 0) { Say "upload failed for $($key): $($out | Out-String)"; exit 1 }
    Say "uploaded $key"
    $uploaded++
}
Say "media: $uploaded file(s) uploaded"

# wrangler pages deploy keeps the _headers file; other upload tools skip it.
$out = & npx.cmd wrangler pages deploy $site --project-name $Project --branch main --commit-dirty=true 2>&1
$deployCode = $LASTEXITCODE
Add-Content -Path $log -Value ($out | Out-String) -Encoding utf8
Say "deploy exit code $deployCode"
if ($deployCode -ne 0) { exit 1 }
exit $runCode
