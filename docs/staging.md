# Staging: Dev and Test before the live city sites

Every change to CivicCast Brief should run on a copy of a city's own data before it
reaches the eight live sites. `ops\stage.ps1` does that in two tiers.

| Tier | What it does | What it can touch |
| ---- | ------------ | ----------------- |
| **Dev** | Runs the city's weekly run on a copy, builds the site, serves it on `127.0.0.1` only. Audio uses the repo's test-tone engine unless `-RealVoice` is given. | The copy, and a local server on the chosen port. No upload. No deploy. |
| **Test** | Runs the weekly run with real voices, uploads changed media under `test/<city>/` in the media bucket, deploys to the one Pages project `civiccast-brief-test`, branch `<city>`. | The copy, the `test/<city>/` R2 prefix, and `civiccast-brief-test`. Nothing live. |

A stage run never writes to the live working folder. It copies the desk, the city
settings, the run state, the episodes and the media to
`C:\dev\civiccast-brief-stage\<tier>-<city>-<yyyyMMdd_HHmmss>` and leaves meeting source
audio (`raw/`) out. It records a fingerprint of the live folder before and after the
run and fails if that folder changed.

## The command

```powershell
pwsh -File ops\stage.ps1 -Tier Dev  -City superior
pwsh -File ops\stage.ps1 -Tier Test -City superior
pwsh -File ops\stage.ps1 -Tier Dev  -City longmont -Port 3210 -Days 7
pwsh -File ops\stage.ps1 -Tier Dev  -City superior -RealVoice
pwsh -File ops\stage.ps1 -Tier Test -City superior -Plan
```

Options:

- `-Repo <checkout>`: the checkout whose `src` runs (default `C:\dev\civiccast-brief`).
- `-Python <path>`: the interpreter (default `<Repo>\.venv\Scripts\python.exe`).
- `-Port <n>`: the Dev server's port (default 3200). Port 3000 is refused.
- `-Days <n>`: the weekly run's window (default 14, the same as the scheduled task).
- `-RealVoice`: Dev renders with Gemini and the LFM3 fallback instead of the test tone.
- `-Plan`: print every path, command, upload target and deploy target, and change
  nothing.

The city working folder is `weekly-<city>` under `C:\dev\civiccast-brief-scratch`;
Longmont's is `weekly-real`.

## Dev

1. Copies the city folder (without `raw/`) to the stage folder.
2. Rewrites `https://media.civiccastbrief.org/<city>/` to the Dev media base in the
   copy's `live\episodes\*.json` (`url`, `chapters_url`, `vtt_url`), so the copied
   episodes play the staged audio. The rest of each file is left byte for byte alone,
   and meeting-video and public-record addresses are not touched. The weekly run decides
   what to build and what to render from `desk.db` and the run folders, not from these
   files, so the rewrite neither rebuilds an episode nor renders its audio again.
3. Runs the same `civiccast_brief.weekly run` command the scheduled task runs, in the
   copy, with `--audio-engine fake` unless `-RealVoice`.
4. After the build, scans every `.html`, `.xml` and `.json` in the built site and stops
   before serving if any still names the live city site or its live media prefix.
5. Copies `live\media` under the built site and starts `python -m http.server` bound to
   `127.0.0.1` only.
6. Checks `/`, one episode page and one media file answer 200, and checks the live
   folder is unchanged.
7. Prints the address and the PID:

```
PASS: Dev stage for superior
copy:  C:\dev\civiccast-brief-stage\dev-superior-20261122_101500
site:  http://127.0.0.1:3200/
PID:   12345
stop:  Stop-Process -Id 12345
```

Nothing is uploaded and nothing is deployed. The server serves the copy only and is
stopped by its own PID.

If the weekly run exits non-zero, Dev still builds and serves whatever site the run
left behind so it can be inspected, but it never prints `PASS`. It prints
`NOT PASSED: weekly run exit <code>`, the `source problem:` lines from the run log, and
the stop command, and exits non-zero:

```
NOT PASSED: weekly run exit 3
source problem: no captions for 2026-11-18
run log: C:\dev\civiccast-brief-stage\dev-superior-20261122_101500\stage-run.log
server PID 12345 is still running (site served for inspection)
stop it with: Stop-Process -Id 12345
```

If the run built no site at all, there is nothing to serve and the script stops with
the exit code and the run log path.

## Test

1. Copies the city folder (without `raw/`) and rewrites the copied episode media
   addresses to `https://media.civiccastbrief.org/test/<city>/`, as Dev does.
2. Runs the weekly run with real voices (Gemini, fallback LFM3). If it exits non-zero,
   the script stops there, before any upload or deploy, naming the exit code and the
   run log, and exits non-zero.
3. Inserts `X-Robots-Tag: noindex` inside the `/*` rule of the built `_headers` (creating
   that rule if it is missing), so every page on the Test site is noindex.
4. Scans every `.html`, `.xml` and `.json` in the built site and refuses to upload or
   deploy if any still names `https://<city>.civiccastbrief.org` or
   `https://media.civiccastbrief.org/<city>/`.
5. Resolves the Cloudflare account id before uploading. In order: the user environment
   variable `CIVICCAST_CF_ACCOUNT_ID`, then a `CLOUDFLARE_ACCOUNT_ID` already set in the
   environment, then the first `CLOUDFLARE_ACCOUNT_ID = '<32 hex>'` line in a
   `weekly-*.ps1` file sitting directly in the city working folder (read only, no
   recursion). Only the id's length and where it came from are printed. If no id is
   found, the script stops before any upload.
6. Uploads only media whose bytes changed, to
   `civiccast-brief-media/test/<city>/<file>`, served at
   `https://media.civiccastbrief.org/test/<city>/<file>`.
7. Deploys to Pages project `civiccast-brief-test`, branch `<city>`.

The Test site is a separate Pages project and branch, so it cannot appear in the live
home page or a live feed. It is noindex, and its media lives under `test/`, never under
the live `<city>/` prefix.

## Safety, enforced before any write

- The Pages project must be `civiccast-brief-test`.
- Every R2 key must start with `test/`.
- The stage copy must not be inside `C:\dev\civiccast-brief-scratch`, and must not be
  the live folder or inside it.
- No scheduled task named `CivicCast Brief weekly*` may be running.
- A Test run must resolve a Cloudflare account id before it uploads anything; with none
  it stops before the first upload. The id is an identifier, not a secret, and only its
  length and source are printed.
- Port 3000 is refused.
- Only the PID this script started is ever named for stopping. The script stops
  nothing else.

The checks live in `ops\stage-lib.ps1` and are tested in `tests\test_stage_script.py`.

## What the weekly run sends outward

`civiccast_brief.weekly run` has no mail, notification or webhook code. It does these
outbound calls while it works:

- fetches the city's records, captions and media from its public portals;
- calls the writer model through the local Ollama address (`127.0.0.1:11434`);
- calls Gemini TTS when the Gemini engine is selected (Dev with `-RealVoice`, and Test);
- lets the article step run its agent, which is told to use the supplied material only
  and to send no email or message;
- downloads site stills.

Upload and deploy are not part of `weekly run`; they are the two steps in
`ops\weekly-city.ps1` and `ops\publish-city.ps1`. A Dev stage performs neither. A Test
stage performs them only against `test/<city>/` and `civiccast-brief-test`.

## Cleaning up

Stage copies are not removed automatically. Each one is a full copy of the desk and
run state without the meeting audio. Delete the `dev-<city>-*` or `test-<city>-*`
folder under `C:\dev\civiccast-brief-stage` when it is no longer needed.
