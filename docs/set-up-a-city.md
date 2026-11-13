# Set up a city

This guide takes one city from nothing to a built site. Each step names the command and the files it
writes. Run the commands from the repository folder with the project's Python
(`uv sync`, then `.venv\Scripts\python.exe`, shown below as `python`).

## Start here: the desk's Find page

Most people set up a city in the editor desk, not with commands. Install the program (next
section), double-click `desk.cmd`, and open **Sources > Find a city's sources**. Type the city's
name and state (and its website, when you know it), press **Search**, tick the sources it found and
press **Add**. The **City** page holds the city's name, short web name, state and contact emails.
The commands further down do the same work from a terminal. They are the other way, for scripts
or for anyone who prefers a terminal.

`desk.cmd` opens your browser by itself, a few seconds after it starts, at `http://127.0.0.1:8765/`.
It keeps its window open for as long as the desk runs: closing that window stops the desk.

## Install

On Windows, `install.ps1` sets up everything below except Chrome, Ollama and the voice engine,
for the signed-in user, with no administrator rights. Double-click `install.cmd` in a copy of the
code, or, with no copy yet, run this in PowerShell:

```
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/scottconverse/civiccast-brief/main/install.ps1 | iex"
```

**Where it installs.** Run from a copy you already downloaded (a folder with `install.ps1` and
`pyproject.toml` together), the program installs inside that folder: the Python environment is its
`.venv` and `desk.cmd` is written there. Run with no copy (the one-line command above), it
downloads the code into `%LOCALAPPDATA%\CivicCastBrief\app` (`-AppDir` picks another folder),
replaces the code already there and keeps its `.venv`. The desk's own data, `desk.db` and the
`city` folder, is in `Documents\CivicCast Brief`. When ffmpeg is not installed by winget, the
script puts it in `%LOCALAPPDATA%\CivicCastBrief\ffmpeg`; yt-dlp goes to
`%USERPROFILE%\.civiccast-brief\record\runtimes\yt-dlp`.

The script installs uv, the Python
packages (uv brings Python 3.12 when the machine has none), ffmpeg (with winget, or the gyan.dev
build checked against its SHA-256) and yt-dlp. It then checks for Chrome and for Ollama with the
writer model, lists what is still missing, and writes `desk.cmd`, which starts the editor desk
(see "Start here" above for how it behaves). TownRecord and the speech code are part of CivicCast
Brief, so nothing is installed for them. Add `-Diarize` for speaker diarization (large).

## What you need first

| Need | Used by | Notes |
|---|---|---|
| Python 3.12 and `uv` | everything | `uv sync` installs the project. |
| `ffmpeg` on PATH | audio, Granicus audio | |
| Ollama at `127.0.0.1:11434` with the writer model | recap, script | The default model is `deepseek-v4.1-flash:1017-cloud`. Pass `--model` to use another. |
| yt-dlp, installed by CivicCast Brief | YouTube capture | Run `python -m civiccast_brief tools --test-video <URL>` once, with any public YouTube meeting video that has captions. It installs yt-dlp into `~/.civiccast-brief/record/runtimes/yt-dlp` and makes it active only after it fetches that video's captions. Not needed for a Granicus city. |
| The `diarize` extra and a Hugging Face token | `--diarize` only | `uv sync --extra diarize`, and set `HF_TOKEN` to a free Hugging Face token that has accepted the pyannote model terms. Without `--diarize`, names come from the roll call only. |
| A voice engine | audio | Dia2 (the default) or Chatterbox, on a Windows PC with an NVIDIA GPU, reached over ssh. Both run as plain Windows Python; none needs Linux. Dia2 setup: [voice-bakeoff.md](voice-bakeoff.md#dia2-setup). |

## 1. Start the city folder

### Let the program find the sources

`city find` reads the city's website and asks the meeting platforms what they list. It uses no AI
model, only plain web requests. Give it the city's name and state, and its website when you know it
(otherwise it guesses addresses like `www.yourcitystate.gov`). When no guess works it says so: search
the web for the city's official website (Loveland, CO is `lovgov.org`, which no guess reaches) and
give that address as `--website` (the **City website** box in the desk):

```
python -m civiccast_brief city find --name "Your City" --state CO --website https://yourcity.gov/ --dry-run
```

It prints each source it can read, with the bodies (council, boards, commissions) the source lists
and how it was found. It also lists the platforms it found but cannot read yet (Legistar, BoardDocs,
CivicClerk) so you can add those by hand later. School boards and charter schools are usually not
linked from a city's site. Add each with `--also "Body name | address"`, where the address is the
page that links its agendas or its Google Drive folder:

```
python -m civiccast_brief city find --name Longmont --state CO --website https://longmontcolorado.gov/ --also "Board of Education | https://www.svvsd.org/about/board-of-education/board-meetings/2026-board-meetings/" --out cities/longmont
```

With `--out`, it writes `sources.json`, plus `city.json` and `roster.json` stubs when the folder has
none. It never overwrites a city.json or roster.json you already have. A source already in
sources.json gains only the bodies it lacked. The council and board members are not typed in: the weekly run fills each body's roster from the
meeting's own records (the roll call in the minutes, member lists on the agenda), keeps each
member's source, and the desk's Roster page shows who came from which record.

Then check that each body really reads:

```
python -m civiccast_brief city check --city cities/longmont
```

For each body, this reads the newest meeting the source lists (agenda, packet or minutes) and prints
its date and a line of its text, or why it could not. Add `--body "Water Board"` to check only some
bodies, and `--captions` to also read YouTube captions (slower; YouTube limits how fast you can ask).
It saves the result to `city-check.json` and exits with an error when any body was not read.

The desk has this check too: **Sources > Check all sources**, or **Check** on one source's row. It
reads each source once (without YouTube captions) and lists each body as READ, with the meeting's
date, title and document link, or NOT READ with the reason. Checking all sources saves
`city-check.json` as the command does; checking one source does not change that file. It can take
a minute, and the button says it is working until the result page opens.

A body's name matches a show or video title that leaves off the city's name (from `city.json`) and a
trailing "Meeting": `LOVELAND CITY COUNCIL MEETING` finds "City Council Meeting November 4, 2026".
On the desk's Sources page, **Show recent titles** lists the body names a Cablecast host's shows use.

The editor desk has the same search under **Sources > Find a city's sources**. It shows what it
found with a checkbox for each source, and saves only the sources you leave checked. A YouTube
channel it spotted but did not offer (none of its newest titles names a body) has an **Add anyway**
button: type the bodies the channel covers and it is added like any other source.

### Or set the folder up by hand

Find where the city puts its meeting videos.

- **YouTube.** Each meeting is a video whose title holds the meeting date.
- **Granicus.** The meeting list is at `https://<host>/ViewPublisher.php?view_id=<n>`. Open it in a
  browser and note the host and the `view_id`. A clip works only when the city keeps captions for it
  (`https://<host>/videos/<clip>/captions.vtt` has text). Some Granicus cities keep none, and
  CivicCast Brief does not transcribe audio.

The city's video platform is never guessed. Say it with `--platform` (`youtube`, `granicus`,
`cablecast` or `none`), or let `setup` take the one it finds (`--granicus`, `--cablecast`, or a
`sources.json` already in the folder). When none or more than one is found, `setup` asks you at a
terminal, and stops with a message when there is no terminal to ask on.

Then run, for a YouTube city:

```
python -m civiccast_brief setup cities/yourcity --slug yourcity --name "Your City" --state "Your State" --platform youtube --contact-email corrections@example.org --podcast-email podcast@example.org --roster-page https://yourcity.gov/council
```

`--state` takes the full name or the two-letter code (`CO`); the folder stores the full name.

The `podcast` settings in `city.json` (category, language, the email Spotify writes to, show art) are
explained in [List a city show on Apple Podcasts and Spotify](podcast-directories.md).

For a Granicus city, use `--granicus yourcity.granicus.com --view-id 2` (instead of
`--platform youtube`); for a Cablecast city, `--cablecast reflect-yourcity.cablecast.tv`. Use `--body` when the
meetings are not a "City Council" (for example `--body "Board of Supervisors"`).

This writes two files and never overwrites one that is already there:

- `cities/yourcity/city.json`: the site, podcast and capture settings. Edit the `about` text, the
  host names and the podcast description here.
- `cities/yourcity/roster.json`: the council roster, with no members yet.

### Add the city's sources

Open the editor desk on the city folder (see step 5 for the command) and go to **Sources**. Add each
place the city's meetings come from, with the bodies (council, boards) it covers:

| Kind | What to enter | Example |
|---|---|---|
| PrimeGov portal | the portal's home address | `https://longmont.primegov.com` |
| YouTube channel | the channel id (`UC` and 22 more letters, digits, `-` or `_`), or the channel's address or `@handle` | `UCH5_wkpLrKYb1JuUk6-UdNg`, `https://www.youtube.com/@CityofLoveland` |
| Granicus video archive | the host and the `view_id` number | `yourcity.granicus.com`, `2` |
| Web page of meeting documents | the page that links the agendas, packets or minutes; optionally the path its meeting pages start with | `https://www.svcmontessori.org/board-meetings`; `/boardmeetings/` |
| Google Drive folder | the public folder's address or id | `https://drive.google.com/drive/folders/15YuyqNfAkNPcbaoT8r6SSedKGyZ6Box2` |
| CivicWeb portal | the portal's address or its tenant, the part before `.civicweb.net` | `https://yourtown.civicweb.net` |
| Cablecast video archive | the archive's host, `<name>.cablecast.tv` (an `https://` address is cut down to its host) | `reflect-cityofloveland-co.cablecast.tv` |

A Cablecast source reads the archive's list of the newest 200 shows that have a video. A body's
meeting is the newest show dated today or earlier whose title names the body (a title may leave off
the city's name and "Meeting", see below); the captions are read when the meeting is run. Find
offers a Cablecast archive that the city's own pages link, with the bodies its show titles name.

A web page source reads the newest dated document it links. When the page links one page per
meeting instead (a school district's board calendar, for example), give the path those pages start
with, and the program opens the newest one and reads the documents on it. A Drive folder source
looks through the folder and its subfolders for the newest agenda.

For a YouTube channel you can paste the id, a `youtube.com/channel/UC...` link, a
`youtube.com/@Handle` link or a bare `@Handle`, or a `youtube.com/user/Name` or `youtube.com/c/Name`
link. A handle or name is turned into the id with one read of the channel's public page (no API key,
no sign-in) and the id is what is saved. If that page cannot be read, the screen says so; paste the
id instead, which YouTube shows under About > Share channel > Copy channel ID.

The screen checks each field, names the one that is wrong, and keeps what you typed. Each source can be edited or removed later. The desk saves them to
`cities/yourcity/sources.json`:

```json
{
  "format": 1,
  "sources": [
    {"id": "primegov:longmont.primegov.com", "kind": "primegov",
     "url": "https://longmont.primegov.com", "bodies": ["City Council"]},
    {"id": "youtube:UCH5_wkpLrKYb1JuUk6-UdNg", "kind": "youtube",
     "channel_id": "UCH5_wkpLrKYb1JuUk6-UdNg", "bodies": ["City Council"]},
    {"id": "granicus:yourcity.granicus.com/2", "kind": "granicus",
     "host": "yourcity.granicus.com", "view_id": "2", "bodies": ["City Council"]}
  ]
}
```

### Pick the models

Go to **Models** in the desk. Each task (recap, judge, article, script, roundup)
has a default model, and any body can use a different one. Out of the box, City Council uses Claude
and every other body uses DeepSeek (`deepseek-v4.1-flash:1017-cloud` through Ollama). The desk
saves the picks to `cities/yourcity/models.json`:

```json
{
  "format": 1,
  "tasks": {
    "recap":   {"default": "ollama:deepseek-v4.1-flash:1017-cloud", "bodies": {"City Council": "claude:claude-opus-5-5"}},
    "judge":   {"default": "ollama:deepseek-v4.1-flash:1017-cloud", "bodies": {"City Council": "claude:claude-opus-5-5"}},
    "article": {"default": "ollama:deepseek-v4.1-flash:1017-cloud", "bodies": {"City Council": "claude:claude-opus-5-5"}},
    "script":  {"default": "ollama:deepseek-v4.1-flash:1017-cloud", "bodies": {"City Council": "claude:claude-opus-5-5"}},
    "roundup": {"default": "ollama:deepseek-v4.1-flash:1017-cloud", "bodies": {"City Council": "claude:claude-opus-5-5"}}
  }
}
```

A pick is `provider:model`. `claude:<model>` runs `claude -p` on your Claude subscription (signed in
with `claude` or `claude setup-token`; no API key: CivicCast Brief removes any `ANTHROPIC_API_KEY`
from the `claude` process). `ollama:<model>` runs through Ollama. With no `models.json`, the defaults above
apply. Code gets a writer for a task with
`civiccast_brief.models.complete_for(city_dir, task, body)`, which returns a function that takes a
prompt and returns the text and a receipt (provider, model, seconds).

## 2. The roster fills itself from the records

You do not type the council or a board in. For each meeting, the weekly run reads the members from
that meeting's own records (the roll call in the minutes in the packet, a member list on the agenda)
and adds them to the body's roster file (`roster.json`, or `rosters/<body>.json`) before any speaker
is named. Each member keeps where a record printed them: the document and page, the line, and the
meeting date. A member already in the file is never changed or removed; a name no record prints in
full is not added; and when no record lists the members, the run says "no member list found in the
records" and leaves the roster as it is. The desk's **Roster** page shows each member and the record
they came from.

A member line in the file looks like this (`seen` and `records` are added by the run):

```json
{"name": "Jane Doe", "first": "Jane", "last": "Doe", "role": "Mayor"}
```

The roster is the only list of speaker names. A line gets a name in three ways:

- the member answered the roll call;
- the chair gave them the floor ("Council member Crist?"), matched to a roster name even when the
  captions mishear it a little; a name that is only mentioned ("I agree with council member Crist")
  names no one;
- with `--diarize`, the line is in the same voice as that member's other named turns;
- with `--diarize`, the line is in the chair's voice and the room answers to that voice as
  "Mayor": at least 3 times another voice says "Mayor" (not "Mayor Pro Tem") and the chair's voice
  speaks next, within 10 seconds, and at least 80 percent of those addresses are not to a pro tem.
  The line then gets the roster's mayor. When the pro tem chairs, the chair's lines stay unknown.

A self-introduction names a voice only when the meeting's own documents print that name: the
agenda, or the packet and minutes pages in `documents.json` (the weekly run fetches them before
the recap; a single run takes `--documents`). Three forms count, in the voice's own lines: "My
name is Morgan Lundy", "I'm Kristin Cote" or "I am Lance Smith", and a name right after a
greeting at the podium ("Good evening, council. Josh Sherman,"). The name must be two or more
words and match the printed words exactly, apart from case; "Nathan Jones" is not "Nathan
Jones-Meyer". The chair's voice, a voice the roll call or a recognition named, a roster member's
name, and a voice that gives two different names are never named this way. A name someone else
says ("invite Jen Uten to the podium") never names a voice. Each introduction is listed under
`introductions` in `speakers.json` with where its name is printed, or why it was not used. No
role is shown for these people. Names not printed stay unknown: the captioner spells visitors'
names ("Espanzoriz", "Jack Bestl"), and a sound match is not trusted ("Morgan Lundy" matched
Jake Marsing at 0.96).

Every other line stays "unknown" for the producer to name. On five Longmont council meetings in
November 2026 this named 22 to 33 percent of caption lines (before, about one line a meeting). A
hand-check of 50 lines found one or two likely wrong. That check read the captions; it was not
checked against the audio. `src/civiccast_brief/meeting/rosters/`
has two filled-in examples (Longmont and Oakland).

## 3. Run one meeting

```
python -m civiccast_brief.meeting run <video id or clip id> --city cities/yourcity --out runs/2026-11-03
```

Add `--agenda agenda.txt` when you have the official agenda as text (its first line is
`Source: <url>`); claims that use a board or street name not in the agenda or roster are dropped.
Add `--diarize` to label caption lines by voice.

The run writes `transcript.json`, `speakers.json`, `recap.json`, `verify-report.json` and
`summary.json`. Every recap claim carries a quote that appears word for word in the transcript, with
its time.

## 4. Write and check the script

```
python -m civiccast_brief.script write --recap runs/2026-11-03/recap.json --out runs/2026-11-03/script.txt
```

`write` also saves `script.json` and runs the check that the script adds no new claims. Exit 1 means
the check failed. Then write again with the report as feedback, or drop the failing lines:

```
python -m civiccast_brief.script check --recap runs/2026-11-03/recap.json --script runs/2026-11-03/script.json --report feedback.txt
python -m civiccast_brief.script write --recap runs/2026-11-03/recap.json --out runs/2026-11-03/script.txt --feedback feedback.txt
python -m civiccast_brief.script repair --recap runs/2026-11-03/recap.json --script runs/2026-11-03/script.json --out runs/2026-11-03/script-final.txt
```

For a weekly roundup, combine the week's recaps first, then write from the combined recap:

```
python -m civiccast_brief.script roundup --recap runs/a/recap.json --recap runs/b/recap.json --out runs/week/recap.json
```

Give the roundup a written title. `title` saves `title.json` next to the recap when a title passes
the same check; with no passing title it exits 1 and the episode keeps its dated title:

```
python -m civiccast_brief.article title --recap runs/week/recap.json --city cities/yourcity
```

### Write the news story

```
python -m civiccast_brief.article write --recap runs/2026-11-03/recap.json --city cities/yourcity --out runs/2026-11-03/article
```

The story has a headline, dek, sections, votes and quotes. Every paragraph, vote and quote cites
recap claims, so each links to its moment in the video. The writer also sees the captions said
around each claim, so the story can name the item and who spoke. The check refuses any number or
name that is not in the cited claims, those captions or the meeting header. A failing draft is
written once more with the report; a part that still fails is dropped. Exit 1 means the saved story
still fails (for example its headline). It saves `article.json` and `article.md` to read.

## 5. Review and publish on the editor desk

```
python -m civiccast_brief.episode stage --db review.db --meeting runs/2026-11-03 --script runs/2026-11-03/script.json
python -m civiccast_brief.article stage --db review.db --recap runs/2026-11-03/recap.json --article runs/2026-11-03/article.json --city cities/yourcity
python -m civiccast_brief.review serve --db review.db --city cities/yourcity
```

`article stage` checks the story again and adds it as an article item with the result. Every item
that passes its checks is published at once, for every body. An item that fails is held in review
and never published by itself.

The editor desk runs at `http://127.0.0.1:8765` (`--port` picks another). `--city` turns on the
Sources and Models pages; without it the desk only reviews items.

An item is an **episode** (speakers, recap, podcast script) or an **article** (headline, dek and
sections of paragraphs, each with its citations; votes, quotes and topics come from the writer and
are not edited on the desk). Each item is in one state:

| State | Meaning | Exported |
|---|---|---|
| review | waiting for the editor | no |
| held | set aside | no |
| killed | will not run until it is edited or redrafted | no |
| published | ready to go out | yes |

The desk's front page is the control room:

- **Problems** lists what needs you: the weekly job's last run if it failed (in the station), items
  that failed their checks, and items still in review.
- **What went out** lists the published items, each with **Edit**, **Unpublish** and **Kill**.
- In the station it also shows the last run, the next run and a queued publish, with a link to the
  Weekly job page and **Run now**.

All items are listed below it, with a tab for each state.

The buttons on an item page:

- **Save** keeps your edits. A published item stays published, so the change goes out; any other
  item goes back to review.
- **Publish** puts the item out with its exact saved text. A killed item cannot be published until
  it is edited or redrafted.
- **Unpublish** (published items) takes the item off the site and holds it.
- **Hold** and **Kill** take an item out of the export.
- In the station, Publish, Save on a published item, Unpublish and Kill rebuild and deploy the site
  about a minute later.
- **Redraft** sends your note and the current draft to the writer (the city's model pick for that
  body) and puts the new draft in review. It runs the same checks as the weekly run: one rewrite
  with the check report, then anything still unsupported is cut. The writer works from the
  meeting's folder in the weekly run, so start the desk with that folder:
  `python -m civiccast_brief.review serve --db desk.db --city cities/yourcity --work runs/weekly`.
  Without `--work` the button says that no writer is connected. A model call takes a few minutes;
  the button shows that it is working. After an episode redraft, press **Regenerate audio**.
- **Regenerate audio** (episodes only) records a request for new audio for the current text.

Each button shows what it is doing while it works, and each page says what happened. The item's
history lists every action.

**Auto-publish.** It is always on. The weekly run stages items with
`civiccast_brief.review.desk.stage_item(db, kind=..., meeting_id=..., title=..., meeting_date=...,
body=..., checks_passed=...)`, which publishes an item that passed its checks at once.

Then export the published items:

```
python -m civiccast_brief.review export --db review.db --out exported
```

The export writes only published items whose text is unchanged since publishing, and removes items
that were pulled back. Each item gets `item.json`, plus `recap.txt` and `script.txt` for an episode
or `article.json` for an article.

**An older review.db.** The first time the desk opens a database from the old producer screen, it
moves the items over: approved items become published (their approval still matches their text),
drafts go to review, and rejected items are killed. Keep a copy of the file before the first run.

## 6. Make the audio

```
python -m civiccast_brief.audio runs/2026-11-03/script.txt --out runs/2026-11-03/audio
```

The default voice is Dia2 (`dia2`). Add `--engine chatterbox` to use Chatterbox.

This writes `episode.mp3`, `chapters.json` and `transcript.vtt`.

## 7. Build the site

Turn each exported item into a site episode, saved in the city folder:

```
python -m civiccast_brief.episode assemble --item exported/<item> --meeting runs/2026-11-03 --script runs/2026-11-03/script.json --audio runs/2026-11-03/audio --media-base-url https://media.example.org --city yourcity --published 2026-11-04T09:00:00-07:00 --out cities/yourcity/2026-11-03.json
python -m civiccast_brief.site build --episodes cities/yourcity --out site --base-url https://yourcity.example.org
```

Add `--article exported/<article item>` to put the published story on the episode page.
`assemble` refuses an item whose recap or script changed after publishing, and a story that is
not published, is for another meeting, or fails the check after desk edits. The site build reads
`city.json` and every episode JSON in the folder (not `roster.json`) and publishes only approved
episodes. Upload `site/` to the static host and the MP3s to the media address.

Optional `city.json` settings for the site:

- `apple_podcasts_url`, `spotify_url`: https addresses of the show once it is listed. The Apple and
  Spotify buttons on the front page and the podcast page appear only when these are set.
- `email_signup`: `true` shows the sign-up form and the "By email" band. Needs `signup_url`
  (see `docs/email-signup.md`).
- `desk_url`: the https address of the editor desk, for example `https://desk.example.org/desk`
  for a station. Every page's footer then links to the desk's sign-in page as "Editor sign-in".

An episode can carry an `article` (headline, dek, sections, votes, quotes, topics). Every paragraph
cites claims from `recap.claims` by position. Without an article the episode page shows the claim
list. Pages: front page, story pages, `/boards/`, `/topics/`, `/archive/` (by month), `/podcast/`,
`/search/`, `/about/` and `/how-this-is-made/`.

Every page loads the Cloudflare Web Analytics beacon (no cookies). The About page and footer say so.

## 8. List the city on the home page

Add the city to `src/civiccast_brief/site/home_cities.json` with `"live": true` once its site is up,
then build and deploy the home page (Pages project `civiccast-brief-home`):

```
python -m civiccast_brief.site home --out home
cd worker/signup && npx wrangler pages deploy ../../home --project-name civiccast-brief-home
```

Only cities with `"live": true` are listed. Use `wrangler pages deploy`: other upload tools skip
the `_headers` file that keeps Cloudflare from changing the pages.

## 9. Run every week

Steps 3 to 7 run by themselves with one command. It reads the city's `sources.json`, finds the
meetings of the last 7 days and runs each one that is new:

```
python -m civiccast_brief.weekly run --city cities/yourcity --work runs/weekly --db review.db --episodes site-in --media media --site-out site --base-url https://yourcity.example.org --media-base-url https://media.example.org
```

For each meeting, in date order:

1. the portal's HTML agenda is saved as `agenda.txt`;
2. `meeting run` makes the transcript, speakers and recap;
3. `script write` writes the script. When the check fails, it writes once more with the check
   report, and then `script repair` drops the lines that fail;
4. the episode goes on the editor desk, published at once when its checks passed;
5. `article write` and `article stage` write the story and put it on the desk.

With two or more meetings done in the week, a roundup follows: a combined recap, its script, a
written title and its episode on the desk.

Then the desk is exported, and every published episode that is not built yet gets its audio, its
episode JSON (with the published story of the same meeting) and its media files, named after the
episode. Last, the site is built into `--site-out`. Leave out all five site settings to stop after
the desk.

**Which meetings run.** A portal meeting needs a YouTube video link; a channel video needs a title
that reads as a meeting and names one of the source's bodies. The same video from two sources is
one meeting, with the portal's agenda. A meeting with no video, a cancelled one, or one whose body
no source covers is listed with the reason and not run.

**Rosters.** Each body needs a roster: `rosters/<body>.json` in the city folder (for example
`rosters/planning-and-zoning-commission.json`), or `roster.json` when its `body` is that body. A
meeting with no roster fails with a message that names the file to add.

**Reruns.** `weekly-state.json` in the work folder records each meeting's steps. Run the same
command again to retry what failed: finished meetings and steps are not run again. Exit code 0
means every runnable meeting and the site finished; 1 means something failed (the output says what).

**Minutes.** The run reads the video captions and the agenda. Meeting minutes are not read yet.

**Schedule it.** To run it every week, add a scheduled task that runs the command, for example on
Windows:

```
schtasks /Create /TN "CivicCast Brief weekly" /SC WEEKLY /D TUE /ST 06:00 /TR "C:\path\to\python.exe -m civiccast_brief.weekly run --city ... (the rest of the command)"
```
