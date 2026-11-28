# CivicCast Brief

CivicCast Brief answers one question for a town: *what did my local government just do?* It reads a city's public meeting videos, agendas, packets and minutes, and makes a cited recap, a news story and a two-host podcast episode for each meeting.
Every paragraph, vote and quote links to the second in the video where it was said. It then builds a static city website with a podcast feed. Version 0.6.1.

[Website](https://scottconverse.github.io/civiccast-brief/) · [Live sites](https://civiccastbrief.org): Longmont, Boulder, Louisville, Lafayette, Lyons, Nederland, Superior and Erie, Colo. · [User manual](docs/user-manual.md) · [Set up a city](docs/set-up-a-city.md) · [Releases](https://github.com/scottconverse/civiccast-brief/releases)

## Published Townlight shared importer

[Townlight Windows 1.8.1.1](https://github.com/townlight/townlight/releases/tag/townlight-windows-v1.8.1.1)
ships the shared reader inside Townlight Runtime. The [full import guide](https://townlight.github.io/manuals/meeting-imports.html)
lists all 31 supported source families, 34 source/format pairs, required saved-response formats,
module handoffs, the CLI/Python/native HTTP contracts, and source-specific limits.
Configure the existing opt-in adapter with a Python executable containing that runtime;
selecting a source alone does not provision a separate server or standalone library.

Brief opts into its existing PrimeGov and Granicus discovery paths with `TOWNLIGHT_IMPORT_PYTHON`; it does not expose all31 sources in a new menu. Existing document/media/caption processing remains the consuming workflow.

For shared Townlight discovery, set `TOWNLIGHT_IMPORT_PYTHON` to the Python
executable containing `townlight_runtime.meeting_imports`. CivicCast Brief
then uses that importer for PrimeGov meeting lists and Granicus ViewPublisher
archives. Brief continues to process the source documents, recordings and
captions through its existing paths.

## What it does

1. Finds new meetings each week from PrimeGov, Granicus, YouTube channels, Cablecast, CivicWeb,
   CivicClerk, CivicPlus, eScribe, Laserfiche WebLink, Vimeo and plain web pages, plus public
   Google Drive folders and OneDrive share links.
2. Makes a transcript with times, from the source's own captions or, when it lists media but no
   captions, by transcribing the audio on your own machine. A speaker is named only when a city
   record prints the name.
3. Fills each body's roster from the meeting's own records, then writes a recap and a news story.
   A checker drops any claim, number or name that is not in the meeting's own words or documents.
4. Writes a two-host podcast script for Pat and Alex and renders it with Gemini TTS, with LFM3 on
   the PC as the fallback; Dia2 and Chatterbox are also available.
5. Builds a static city site: stories with a picture on each card, an audio player with volume and
   mute, transcript pages, board, topic and monthly archive pages, search, a sitemap and a
   podcast feed.
6. Deploys the site to Cloudflare Pages and the audio to R2, or copies both to any web server.

Editors work in the desk, a local web app (double-click `desk.cmd`). An item that passes every check
is published on its own; editors can edit, unpublish, hold or kill anything from the desk.

The weekly run keeps to a time budget: `--time-budget-hours` (6 by default, `0` for none) stops it
starting new meetings once that time has passed, and the meetings it does not reach wait for the
next run. On one PC, weekly runs take turns for Claude, so there is one Claude run at a time.

## Quick start

On Windows 11, download `civiccast-brief-install-v0.6.1.zip` from
[the latest release](https://github.com/scottconverse/civiccast-brief/releases/latest), unpack it,
and double-click `install.cmd`. The `install.ps1` in the zip is pinned to that release, so it
downloads that exact version.

Or, with one command (which installs the newest code on `main`):

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/scottconverse/civiccast-brief/main/install.ps1 | iex"
```

It installs the program and the tools it runs, then says what is still missing (Chrome, Ollama).
Then run `.\desk.cmd` and follow [Set up a city](docs/set-up-a-city.md). You need Google Chrome,
Ollama for the default writing model, and Claude Code signed in to a Claude subscription for the
weekly story writer. The audio runs on Gemini TTS (a Google API key) or LFM3 through llama.cpp on
the PC; an NVIDIA GPU is needed only for the optional Dia2 and Chatterbox engines.

## Documentation

- [User manual](docs/user-manual.md): new users, how to set up, editors, technical users and the architecture.
- [Set up a city](docs/set-up-a-city.md): install, city folder, weekly run, hosting, station.
- [Voice bake-off](docs/voice-bakeoff.md): how the engines were compared, and what runs today.
- [Staging](docs/staging.md): run a change on a copy (Dev, then Test) before the live sites.
- [Email sign-up](docs/email-signup.md) and [podcast directories](docs/podcast-directories.md).
- [Landing page source](website/README.md).

## Project status

Version 0.6.1. Actively developed. The weekly run publishes eight live Colorado town sites
(Longmont, Boulder, Louisville, Lafayette, Lyons, Nederland, Superior and Erie). Built and tested
on Windows 11.

Not yet:

- The email sign-up Worker (`worker/signup`, double opt-in) is built but not connected to a sending
  account, and not on the Longmont site.
- The podcast feed follows the Apple Podcasts and Spotify formats but is not listed in either directory.
- The cloud station with a Windows PC worker is built; the [Hostinger guide](docs/run-on-hostinger.md)
  has not been run on a Linux server.

## What it is not

CivicCast Brief is not full community coverage. It has no beats, no daily scans, no records requests,
no opinion pieces, no sponsor ledger or underwriting, no hosted multi-city network, and no ads or
cross-site tracking. It covers one city's meetings at a time.

## Privacy

City sites, the home page and the sign-up pages use Cloudflare Web Analytics to count visits. It sets
no cookies and does not track readers across sites. Emails carry no analytics. The audio player keeps
your place in the browser tab only.

## Contributing

Report problems and ideas in [GitHub issues](https://github.com/scottconverse/civiccast-brief/issues).
Tests run on GitHub Actions (`uv run ruff check`, `uv run ruff format --check`, `uv run pytest`).
The build plan is in [PLAN.md](PLAN.md).

## Family

Four related projects by the same author sit alongside this one:

- CivicCast (civiccast-native): a full civic station.
- TownReporter: a full community newsroom.
- TownRecord: a records portal.
- CivicNewspaper: a static paper publisher.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
