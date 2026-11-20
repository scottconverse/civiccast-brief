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
Lafayette, Longmont, Louisville and Lyons.

`city-check.json` (a city's setup check) is left out because it copies agenda text that includes
staff contact details; the city setup writes it again.
