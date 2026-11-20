# Operations: the weekly city runs

These are the scripts and city settings the live civiccastbrief.org sites run on. The copies here
are the record; each city's working folder on the run PC holds its own copy and its data.

## Layout on the run PC

Each city has a working folder (for example `C:\dev\civiccast-brief-scratch\weekly-boulder`;
Longmont's is `weekly-real`) with:

- `cities\<city>\` — the city settings from `ops/cities/<city>/` (city, roster, rosters, sources).
- `desk.db` — the editor desk. Nothing reaches a site unless it is published there.
- `runs\` — meeting work folders. `live\` — episodes, media, the built site and run logs.

The desk, runs and live folders are data, not code, and are not in this repository.

## Scripts

- `weekly-city.ps1 -City <city> -Here <working folder>` — the weekly run: find new meetings, make
  the episodes, build the site, upload new media to R2 (bucket `civiccast-brief-media`), and
  deploy the site to Cloudflare Pages (`civiccast-brief-<city>`).
- `publish-city.ps1 -City <city> -Here <working folder> [-Repo <checkout>]` — rebuild and deploy one
  city's site from what is already published, without a weekly run.

Both read the Cloudflare deploy token from Windows Credential Manager
(`CivicCastBrief/cloudflare-deploy`) and the account ID from the user environment variable
`CIVICCAST_CF_ACCOUNT_ID`. Neither is stored here.

Scheduled tasks named `CivicCast Brief weekly (<City>)` run the weekly script for Boulder,
Erie, Lafayette, Longmont, Louisville, Lyons, Nederland and Superior.

`city-check.json` (a city's setup check) is left out because it copies agenda text that includes
staff contact details; the city setup writes it again.

## Nightly backup

`backup-brief.ps1` copies the working files to `D:\CivicCast-Brief-backups`. The scheduled task
`CivicCast Brief backup` runs it every night at 11 PM, while the user is logged in. It runs the
copy kept at `C:\dev\civiccast-brief-scratchackupackup-brief.ps1`.

- Each run makes `daily\yyyy-MM-dd\` with every `weekly-*` folder and `publish-city.ps1`.
- `.wav` files are left out: the raw city meeting audio (it can usually be downloaded again)
  and the voice-render scratch files. Finished episode mp3s are kept. A run is about 0.8 GB.
- Each `desk*.db` is copied with the SQLite backup API and checked with `PRAGMA integrity_check`.
- Kept: the newest 30 runs, plus the oldest run of each month for a year.
- `backup.log` has every run; `last-result.txt` has the last result (`OK ...` or `FAIL ...`).
- `-DryRun` shows what it would do; `-Source` and `-Dest` point it somewhere else for a test.

### Restore

1. Stop the weekly task for the city (or wait until no run is going).
2. Copy the city folder back:
   `robocopy D:\CivicCast-Brief-backups\daily\<date>\weekly-<city> C:\dev\civiccast-brief-scratch\weekly-<city> /E`
   This brings back the desk database, transcripts, documents, drafts and settings. It does not
   delete newer files.
3. The raw meeting audio is not in the backup. A re-run that needs it downloads it again.
