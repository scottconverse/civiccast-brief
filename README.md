# CivicCast Brief

CivicCast Brief is a lite, self-hosted tool that answers one question: what did my local government just do?
It takes a city meeting video and produces a speaker-named transcript, a short written recap with every
sentence cited to a timestamp or page, a two-host podcast episode, and a static city website with articles,
an audio player, transcript pages, a podcast feed and an email sign-up. A human producer approves each
episode before anything is published. Nothing publishes on its own. Voices come only from free, open-source
engines; no hosted text-to-speech is used.

Status: in development. The pipeline runs end to end on real meetings; the default voice is not picked yet. No release yet.

To set up your own city, follow [docs/set-up-a-city.md](docs/set-up-a-city.md).

## What it does

1. Captures a city meeting video (YouTube, PrimeGov and Granicus first).
2. Produces a transcript with timestamps and speaker names.
3. Writes a short recap to a wire-service standard: attributed claims, votes, public comment, and every sentence cited.
4. Writes a two-host podcast script from the approved recap, with an on-air disclaimer, chapters and a transcript.
5. Renders the script to audio with a free, open-source voice engine.
6. Builds a static city site: news stories with every paragraph cited to the meeting video, board, topic and monthly archive pages, audio player, transcript pages linked to the source video, search, a podcast page, sitemap and podcast feed. A separate command builds the civiccastbrief.org home page that lists the live cities.
7. Runs email sign-up with double opt-in, and sends new-article emails.

A producer reviews speakers, recap and script on a local screen, then approves or rejects each episode.
Only approved items reach the export.

## What it is not

CivicCast Brief is not full community coverage. It has no beats, no daily scans, no dark desk, no records
requests, no opinion pieces, no sponsor ledger or underwriting, no hosted multi-city network, no ads or
cross-site tracking, and no live server features. It covers one city meeting at a time.

## Privacy

City sites, the home page and the sign-up pages use Cloudflare Web Analytics to count visits. It sets
no cookies and does not track readers across sites. Emails carry no analytics. The audio player keeps
your place in the browser tab only.

## Family

Four related projects by the same author sit alongside this one:

- CivicCast (civiccast-native): a full civic station.
- TownReporter: a full community newsroom.
- TownRecord: a records portal.
- CivicNewspaper: a static paper publisher.

## Target machine

- Primary: Windows 11, NVIDIA RTX 5070 Ti 16 GB, 32 GB RAM.
- Secondary: Windows on AMD integrated graphics, with CPU-only voice.
- Capture runs on the operator's own machine.

Full test fleet and GPU notes are in [PLAN.md](PLAN.md).

## Build order

The build runs in eleven phases. Each phase is listed in [PLAN.md](PLAN.md) with its done-when check.

- Phase 0: Repo and baseline
- Phase 1: Voice bake-off
- Phase 2: Script writer
- Phase 3: Voice adapter and audio finish
- Phase 4: Transcript, speaker names, recap
- Phase 5: Producer screen
- Phase 6: Static site and feed
- Phase 7: Hosting and directories
- Phase 8: Email
- Phase 9: Roundups and more hosts
- Phase 10: Second city and release

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
