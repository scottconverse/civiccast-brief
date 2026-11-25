# Changelog

All notable changes to CivicCast Brief. Versions follow the repository's release tags.

## 0.5.0 — 2026-11-24

### The podcast

- Episodes are mastered at -18 LUFS (was -16) so the limiter no longer turns speech down by more
  than 3 dB; one constant gain is applied to the whole episode.

### Names and transcripts

- The script check treats a sentence-opening "Tonight" as an opener, not a name.
- The script check also accepts "Take", "We'll", "We've", "You'll", "You've", "They'll",
  "They've", "I'll" and "I've" as sentence openers, not names.

### The site

- A second meeting of the same body on the same day now gets its own episode id instead of being
  left off the site.

### Running it

- A meeting whose video is a scheduled live stream or premiere that has not started yet is
  reported as "not run: the video is a live event that has not started yet" (no longer a failure
  or exit 1) and is picked up again on a later week once it has aired.
- CivicClerk video and document requests try three times, waiting 5 s and then 15 s, before the
  weekly run gives up, so one brief server error no longer makes it miss a city's meetings.

### Staging

- A change can run on a copy of a city before the live sites. `ops\stage.ps1` runs a Dev stage (a
  copy on this PC, served on `127.0.0.1` only, with test-tone audio unless `-RealVoice`) and a
  Test stage (real voices, media under `test/<city>/` in R2, deployed to the private
  `civiccast-brief-test` Pages project). Neither tier can write the live folder. See
  [docs/staging.md](docs/staging.md).
- The Test stage finds the Cloudflare account id from the `CIVICCAST_CF_ACCOUNT_ID` environment
  variable, a `CLOUDFLARE_ACCOUNT_ID` already in the environment, or a `weekly-*.ps1` in the city
  folder, and stops before uploading anything if it finds none (the variable is empty on this PC).
- A wrangler warning on stderr no longer stops the Test stage after a good deploy.

## 0.4.0 — 2026-11-21

### The podcast

- The hosts are Pat (speaker S1) and Alex (speaker S2). A saved script that still names the old
  hosts, or carries the old spoken line, still passes the check; that line is dropped before the
  checks, the audio and the pages.
- New voice engines. Gemini TTS is the weekly run's first engine (voices Bodi and Sola), with
  Gemini Flash-Lite TTS as its own fallback model, then LFM3 through llama.cpp on the PC. The
  `audio` command takes `--engine` and `--fallback-engine`; Dia2 is still the default when no
  engine is named.
- Finished episodes land at -16 LUFS integrated and under -1.5 dBTP true peak. An MP3 whose
  decoded peak overshoots is re-mastered, and the episode's length and the chapter and transcript
  times do not move.

### Stories

- Meeting stories are written the TownReporter way: the report-local-news skill runs in its own
  folder with the record supplied, and its draft is checked claim by claim. The story leads with
  the news, prints a name only when a roster or a document holds it, and may cite document pages
  as well as video moments.
- A story can cite "p." page ranges, and a run of document pages gets one link rather than one
  link per page. One link per run of video moments works the same way.
- The check keeps true vote lines, drops a link to a video moment that does not say what the
  sentence claims, and judges each retry draft against the claims that draft was written with.
- A weekly roundup story is built from the week's meeting stories (`article from-stories`), and a
  roundup title is printed only when it passes the same check.

### Names and transcripts

- Each body's roster fills itself from the meeting's own records (the roll call in the minutes, a
  member list on the agenda) before any speaker is named, and each member keeps the record that
  printed them.
- Speaker naming fixes: a voice is not named after a member it refers to by title; a mention is
  counted separately from a named turn; the diarizer's misfiled hand-off does not take a member's
  name; a stray diarized line does not take a voice's name; a lone surname that sounds like a
  sitting member is not matched; a plural title stays in front of a checked name; a seat holder
  prints once; a meeting's name fixes apply only to its own pages.
- A portal record joins the channel video for the same meeting, and a document that failed to
  fetch is retried on the next run.

### The site

- Every story card has a picture: a frame from the meeting's own media at the story's lead cited
  moment, or an original topic graphic when there is no video. A broken download or a missing
  ffmpeg leaves the graphic and never fails the build.
- The episode player has a volume slider and a mute button, and the level is kept for the next
  episode.

### Running it

- The weekly run scripts and the live cities' settings are in `ops/`, with a nightly backup script
  and restore notes.
- Weekly runs can cap publish retries, keep finished episodes, rebuild audio that is not whole,
  and keep the right story when a meeting was posted twice.
- VerdictLite config in `vlite.json`: it records each commit's verification and never blocks a
  commit (observe mode).

### Known limits

- 56 of the 57 published caption files still label the hosts with the old names; the audio and
  episode data use Pat and Alex.
- Speaker-label checks could be replayed on only 3 of 46 published meetings, because the others
  kept no voice data.

## 0.3.0 — 2026-11-17

Eight Colorado town sites are live on [civiccastbrief.org](https://civiccastbrief.org): Longmont,
Boulder, Louisville, Lafayette, Lyons, Nederland, Superior and Erie.

### New meeting sources

- CivicClerk, eScribe and Vimeo video readers. Each reads a source's public listing, resolves the
  meeting's direct media address (MP4 or HLS), and captures the meeting from it.
- The readers are wired into the weekly finder, and `vimeo` is a new source kind.
- `meeting run` takes `--media-url`, `--captions-url` and `--title`, so a meeting from the source's
  own media address can be run directly. Video ids that begin with a dash are passed to the
  meeting command safely.

### Captions or transcription

- When the source publishes a caption file (CivicClerk's closed-caption URL, eScribe's iSiLive
  `.vtt`, a Vimeo text track), that file is the transcript.
- When it lists media but no captions (Erie and Superior's CivicClerk MP4s), the audio is fetched
  with ffmpeg and transcribed on this machine. No hosted model is called.

### Finding meetings

- Lyons Board of Trustees videos are read with a Trustees keyword, "&" is read as "and", and
  dateless videos are joined to agendas by title word and then by elimination.
- A dateless video may now join an agenda within a two-day lookback window.

### Titles and the site build

- Meeting dates written with ordinals ("1st", "15th") are read from video titles, and body names
  drop a trailing date.
- The weekly publish copies `city.json` into the episodes folder, so a city site builds from the
  run every week.

### Home page

- Boulder, Louisville, Lafayette, Lyons, Nederland, Superior and Erie were added to the home page
  alongside Longmont.

## 0.2.0 — 2026-11-14

The Longmont site is live at https://longmont.civiccastbrief.org, with an editor desk and a weekly
run that finds, writes and publishes meetings on its own.

### What's new since 0.1.0

- **Hosted site and audio.** The weekly publish builds the site and deploys it to Cloudflare Pages,
  with audio and media on R2. The front page is laid out story first, and each story stands on its
  own page.
- **Editor desk.** Sign-in, a control room with Problems, What went out and Unpublish, and a
  Redraft button. A story edited and published on the desk replaces the copy its episode was built
  with. Auto-publish holds anything that fails its checks.
- **Weekly run.** It reads each meeting's packet and minutes, and stories can cite document pages.
  It resumes after a stop, and runs two videos of one meeting only once. Undated and future videos
  wait. An episode unpublished or killed on the desk leaves the site.
- **More meeting sources.** PrimeGov, Laserfiche, CivicPlus, CivicClerk, eScribe, CivicWeb
  (iCompass), Cablecast (Reflect), OneDrive and Drive shares, web pages, and YouTube's own pages
  when other readers fail. `city find` finds a new city's sources without a model.
- **Speakers and rosters.** Board rosters come from the town's own records (PrimeGov member lists,
  board pages and meeting documents). More speakers are named, still only from printed records.
- **Voice.** Dia2 is the default voice. Step-Audio-EditX is removed.
- **Install on another Windows PC.** `install.ps1` / `install.cmd` installs everything in one step
  and writes `desk.cmd`. A fresh Windows runner on GitHub tests it.
- **Station and PC worker.** The station runs the desk at `/desk` and the weekly job on a schedule.
  A Windows PC worker can fetch captions and make audio for a cloud station.
- **Email sign-up worker** (`worker/signup`, double opt-in) is built and tested. It is not on the
  Longmont site and not connected to a sending account yet.
- **Docs and landing page.** A user manual for new users, editors and technical users, with
  architecture diagrams, and a project page at https://scottconverse.github.io/civiccast-brief/.
  The README is rewritten.

### Not in this release

- No Apple Podcasts or Spotify listing and no Brevo sending account yet.
- `docs/run-on-hostinger.md` has not been run on a Linux server.

## 0.1.0 — 2026-11-07

First release: a public meeting video in, a cited recap, a two-host podcast episode and a static
city site out.

### What it does

- **Capture and transcript.** Pulls a meeting (YouTube or Granicus) through TownRecord and makes a
  transcript with speaker turns. Speakers are named only from an exact roll-call match; everyone
  else stays "unknown".
- **Cited recap.** Every claim carries a verbatim quote and a time in the meeting video. A checker
  drops claims it cannot match.
- **Two-host script.** Maya and Theo talk through the recap. A no-new-claims checker refuses lines
  the recap does not support.
- **Audio.** Step-Audio-EditX is the default voice; Dia2 is the backup, and Chatterbox is also
  available. Output: MP3 at -16 LUFS, chapters, and a VTT transcript.
- **Producer screen.** A local page where a person reads, edits and approves each episode. Only
  approved episodes can be exported.
- **Site and feed.** A static site per city (episode pages, transcripts, search, corrections,
  sitemap) and a podcast RSS feed.

### Not in this release

- Hosting the site and audio online, Apple Podcasts and Spotify listings, and email sign-up are not
  built yet.
