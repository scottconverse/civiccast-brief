# CivicCast Brief — build plan

Drafted 2026-11-05 from a read of bozeman.civiccast.ai, civiccast.ai and eleven scottconverse repos
(civiccast-native, civiccast, TownReporter, TownRecord, CivicNewspaper, textflowkit, Describer-Workbench,
civic-scanner, civic-newsroom, civic-transparency-toolkit, opinionforge-ai) plus the Craft Anything rebuild.
Repos were read, not run. Items marked "not checked" were not verified.

## 1. What it is

A lite, self-hosted product that answers "what did my local government just do?"

Meeting video in -> speaker-named transcript -> short written recap -> two-host podcast episode ->
a static city site (articles, player, transcript pages, podcast feed, email sign-up).
One human approves each episode before it publishes. Nothing publishes on its own.

Family: CivicCast (station, civiccast-native) · **CivicCast Brief** (this) · TownReporter (full newsroom) · TownRecord (records portal).

Public home: civiccast.org, one subdomain per city (longmont.civiccast.org). Code and docs: civiccast.io.
Repo: github.com/scottconverse/civiccast-brief. License: Apache-2.0.

### In scope (matches what civiccast.ai ships)
- Ingest from YouTube, PrimeGov, Granicus; Swagit and EvoGov later.
- Transcript with timestamps and speaker names.
- Written recap to a wire-service standard: attributed claims, votes, public comment, every sentence cited to a timestamp or page.
- Two-host conversational podcast episode with an on-air disclaimer, chapters and a transcript.
- Producer review screen: fix speakers, edit, approve or reject.
- Static city site: front page, board pages, article pages with audio player, transcript pages linked to the source video, search, corrections page, "how this is made" page, sitemap, structured data, share images.
- Podcast feed with full tags; one-time submission to Apple and Spotify.
- Email sign-up with double opt-in; new-article emails.
- Weekly roundup episodes.
- Per-city setup so a second city runs without help.

### Out of scope (this is what makes it lite)
TownReporter beats and daily scans, dark desk, records requests, opinion pieces, sponsor ledger and
underwriting, hosted multi-city network, analytics or tracking, live server features.

## 2. Target machine

Primary: Windows 11, NVIDIA RTX 5070 Ti 16 GB (Blackwell, compute 12.0, needs a PyTorch CUDA 12.8+ wheel), 32 GB RAM. Test box: nvideablackwell (Ryzen 7 7800X3D, driver 610.88).
Secondary: Windows on AMD integrated graphics (Halo class) — CPU-only voice (Kokoro); other engines not checked.
Capture runs on the operator's own machine (YouTube blocks data centres; TownRecord spec §2).

Memory note: a 27–35B writer LLM will not share 16 GB VRAM with a TTS model. Run write then voice sequentially,
or use a smaller writer model. Decide in Phase 2.

### Test fleet

| Box | CPU / GPU | RAM | Role |
|---|---|---|---|
| nvideablackwell | Ryzen 7 7800X3D / RTX 5070 Ti 16 GB | 32 GB | primary voice + GPU tests |
| halo | Ryzen AI Max (Strix Halo) / Radeon 8060S | 64 GB | dev box, CPU-only voice test |
| beelink64gb | Ryzen 7 255 / Radeon 780M | 48 GB | spare |
| r7 | Ryzen 7 8745HS / Radeon 780M | 32 GB | low-end AMD check |
| r9-machine | Ryzen 9 7940HS / Radeon 780M | 26 GB | spare |

## 3. Stack

- Python 3.12, one package `civiccast_brief`, CLI first, small local web UI for the producer screen.
- SQLite for state. Files on disk for media.
- textflowkit as a dependency for transcription and diarization.
- Jinja2 templates for the static site (ported from CivicNewspaper's templates, not called as Rust).
- ffmpeg for audio finishing.
- Voice engines behind one adapter interface; engine chosen by the Phase 1 bake-off.
- Sign-up endpoint as a small separate worker (Craft's Cloudflare worker or a Python equivalent); the site itself stays static.
- CI from the first commit with the docs-only filter (Scott's CI rule).

## 4. Where each piece comes from

| Piece | Source | State |
|---|---|---|
| YouTube and PrimeGov capture, readiness, pacing | TownRecord `core/src/townrecord/{capture,video,adapters}` | exists, not run |
| Granicus adapter | civiccast-native | exists, port |
| Transcript, captions-first, audio fallback | textflowkit | exists (v0.1.19) |
| Speaker labels (diarization) | textflowkit (pyannote) | exists; pyannote model terms not checked |
| Speaker names | — | new |
| Grounding and citation rules | TownRecord `ai/grounding.py`; TownReporter meeting-draft prompts | exists, adapt |
| Recap prompt | — | new (lite, wire standard) |
| Two-host script writer | — | new, the core |
| Voice adapter + free open-source engines | — | new |
| Loudness, MP3, chapters, VTT | civiccast-native `podcast/service.py`; Describer-Workbench mixer | exists, port |
| Producer screen | TownReporter desk as the model | new, small |
| Site templates, corrections page, RSS, manifest | CivicNewspaper `compiler.rs` + `templates/` | exists, port to Jinja |
| Audio player with speed and resume; page look | Craft kit `src/client/meeting.js`, styles | exists, take |
| Sitemap, JSON-LD, transcript pages, search | — | new, small |
| Podcast feed tags | civiccast-native `podcast/rss.py` + Craft `feeds.mjs` | extend |
| Email double opt-in, SMTP, retries | civiccast-native `subscribe/` | exists, port |
| Hosting | CivicNewspaper `publisher.rs` (here.now, GitHub Pages, Netlify) as reference; static upload | exists as reference |

## 5. Phases (podcast first)

Each phase ends only when its "done when" is shown, not claimed.

### Phase 0 — Repo and baseline (S)
- Create `scottconverse/civiccast-brief`: README (scope, family, target machine), Apache-2.0, `.gitignore`, CI with docs-only filter, this plan as PLAN.md.
- On nvideablackwell: confirm PyTorch sees the GPU, record driver and CUDA versions.
- Run TownRecord's tests and one Longmont capture to learn what works today.
- Done when: the repo exists, the GPU check passes, and a written note lists what TownRecord does and does not do today.

### Phase 1 — Voice bake-off (M)
- One 90-second two-host script from a real Longmont meeting (hand-written for this test).
- Engines: Dia2 (Apache-2.0, native two-speaker), Kokoro (Apache-2.0, CPU-capable, stitch turns), Chatterbox Turbo (MIT, cloning, watermarked), Qwen3-TTS (Apache-2.0 per article, not confirmed on the model page), VibeVoice (MIT tag, model card says research use only; emits an audible disclaimer and watermark).
- Score: runs on the target box; naturalness; civic text (dollars, dates, "B-3", "Chapter 38", names); speed; install effort; licence verdict.
- Then one full 5–7 minute script on the top two for stability.
- Done when: Scott has listened to all five on the same script and a default and a fallback are recorded in `docs/voice-bakeoff.md` with the licence verdicts.

### Phase 2 — Script writer (M)
- Input: an approved recap. Output: two-host dialogue with intro, on-air disclaimer, chapter marks, outro.
- Rule: no claim the recap lacks; a checker rejects scripts that add facts.
- Decide the writer model and the sequential load order (writer then voice).
- Done when: one real meeting's script reads naturally and passes the no-new-claims check.

### Phase 3 — Voice adapter and audio finish (M)
- One adapter interface; chosen engine behind it; turn stitching with pauses for single-voice engines.
- Intro/outro, loudness normalisation, MP3, chapters.json, VTT.
- Done when: one episode plays in a podcast app from a local feed, with chapters, disclaimer and correct loudness, and Scott has heard all of it.

### Phase 4 — Transcript, speaker names, recap (M)
- Capture via TownRecord; transcript via textflowkit with diarization.
- Name speakers from the agenda and roll call; unknowns stay "unknown".
- Lite recap prompt with citations.
- Done when: five meetings produce cited drafts with correct or "unknown" speakers, hand-checked.

### Phase 5 — Producer screen (M)
- Local web page: fix speakers, edit recap and script, approve or reject, regenerate audio.
- Done when: an unapproved item cannot reach the export; an approved one can.

### Phase 6 — Static site and feed (L)
- Port CivicNewspaper templates to Jinja; add Craft's player and page look.
- Add sitemap, JSON-LD (NewsArticle, PodcastEpisode, PodcastSeries), share images, transcript pages linked to video timestamps, search, corrections, "how this is made".
- Full podcast feed tags: duration, art, chapters, transcript, article text.
- Done when: a site built from 30 days of Longmont passes a link-by-link click-through and the feed validates in a feed validator.

### Phase 7 — Hosting and directories (S)
- Upload to a static host; audio on the same or an S3-style host.
- Submit once to Apple Podcasts and Spotify.
- Done when: longmont.civiccast.org is live and both apps list the show.

### Phase 8 — Email (M)
- Port civiccast-native double opt-in; sign-up worker; sender.
- Done when: a test address signs up, confirms, and receives a new article.

### Phase 9 — Roundups and more hosts (M)
- Weekly multi-meeting roundup episode. Granicus adapter; Swagit and EvoGov if a target city needs them.
- Done when: a weekly roundup and a Granicus city both run end to end.

### Phase 10 — Second city and release (M)
- Per-city config, setup command, docs, release on GitHub.
- Done when: someone else sets up their own city without help.

## 6. Decisions made
- Name: CivicCast Brief. Public site civiccast.org, code civiccast.io. Buy civiccastbrief.com/.org and redirect.
- Licence: Apache-2.0.
- Voice: free and open-source engines only. No hosted TTS.
- Stack: Python, SQLite, static site, separate sign-up worker.
- Capture runs on the operator's machine.
- No tracking or analytics on city sites.

## 7. Open decisions (decide when reached)
- Writer LLM and how it shares the GPU with voice (Phase 2).
- Mail provider and audio host (Phases 7–8).
- Whether episodes carry VibeVoice's built-in audible disclaimer if that engine wins (Phase 1).
- Whether the on-air disclaimer text follows civiccast.ai's wording or your own (Phase 2).

## 8. Risks and things not checked
- TownRecord's real state (README says no code; core has ~30,800 lines). Phase 0.
- Blackwell support in the TTS repos' pinned wheels. Phase 0.
- Dia2 stability on a 5–7 minute script. Phase 1.
- pyannote model terms for an open-source product. Phase 4.
- Speaker naming accuracy on real meetings. Phase 4.
- Whether any civic-newsroom prompt already covers the recap. Not checked.

## 9. First three actions
1. Create the repo (Phase 0).
2. Run the GPU check on nvideablackwell.
3. Install Kokoro and Dia2 in a scratch folder on nvideablackwell and render the test script (Phase 1 start).
