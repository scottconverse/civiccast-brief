# Changelog

All notable changes to CivicCast Brief. Versions follow the repository's release tags.

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
- Episodes are AI-generated. Check the official meeting record before you rely on anything they say.

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
- Episodes are AI-generated. Check the official meeting record before you rely on anything they say.
