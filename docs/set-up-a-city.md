# Set up a city

This guide takes one city from nothing to a built site. Each step names the command and the files it
writes. Run the commands from the repository folder with the project's Python
(`uv sync`, then `.venv\Scripts\python.exe`, shown below as `python`).

## What you need first

| Need | Used by | Notes |
|---|---|---|
| Python 3.12 and `uv` | everything | `uv sync` installs the project. |
| `ffmpeg` on PATH | audio, Granicus audio | |
| Ollama at `127.0.0.1:11434` with the writer model | recap, script | The default model is `deepseek-v4.1-flash:1017-cloud`. Pass `--model` to use another. |
| yt-dlp, installed by CivicCast Brief | YouTube capture | Run `python -m civiccast_brief tools --test-video <URL>` once, with any public YouTube meeting video that has captions. It installs yt-dlp into `~/.civiccast-brief/record/runtimes/yt-dlp` and makes it active only after it fetches that video's captions. Not needed for a Granicus city. |
| The `diarize` extra and a Hugging Face token | `--diarize` only | `uv sync --extra diarize`, and set `HF_TOKEN` to a free Hugging Face token that has accepted the pyannote model terms. Without `--diarize`, names come from the roll call only. |
| A voice engine | audio | Chatterbox or Dia2. The default voice is not picked yet, so the engine setup is not written up here. |

## 1. Start the city folder

Find where the city puts its meeting videos.

- **YouTube.** Each meeting is a video whose title holds the meeting date.
- **Granicus.** The meeting list is at `https://<host>/ViewPublisher.php?view_id=<n>`. Open it in a
  browser and note the host and the `view_id`. A clip works only when the city keeps captions for it
  (`https://<host>/videos/<clip>/captions.vtt` has text). Some Granicus cities keep none, and
  CivicCast Brief does not transcribe audio.

Then run, for a YouTube city:

```
python -m civiccast_brief setup cities/yourcity --slug yourcity --name "Your City" --state "Your State" --contact-email corrections@example.org --podcast-email podcast@example.org --roster-page https://yourcity.gov/council
```

The `podcast` settings in `city.json` (category, language, the email Spotify writes to, show art) are
explained in [List a city show on Apple Podcasts and Spotify](podcast-directories.md).

For a Granicus city, add `--granicus yourcity.granicus.com --view-id 2`. Use `--body` when the
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
| YouTube channel | the channel id: `UC` and 22 more letters, digits, `-` or `_` | `UCH5_wkpLrKYb1JuUk6-UdNg` |
| Granicus video archive | the host and the `view_id` number | `yourcity.granicus.com`, `2` |

A channel handle such as `@yourcity` is not a channel id; YouTube shows the id under About > Share
channel > Copy channel ID. The screen checks each field, names the one that is wrong, and keeps what
you typed. Each source can be edited or removed later. The desk saves them to
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

Go to **Models and auto-publish** in the desk. Each task (recap, judge, article, script, roundup)
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

## 2. Fill in the roster

Open the city's own council page and add each member to `roster.json`, with the name exactly as the
page prints it, and set `retrieved` to today's date:

```json
{"name": "Jane Doe", "first": "Jane", "last": "Doe", "role": "Mayor"}
```

The roster is the only source of speaker names. A caption name counts only when it matches a roster
name exactly. Every other voice stays "unknown" for the producer to name. `src/civiccast_brief/meeting/rosters/`
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

## 5. Review and publish on the editor desk

```
python -m civiccast_brief.episode stage --db review.db --meeting runs/2026-11-03 --script runs/2026-11-03/script.json
python -m civiccast_brief.review serve --db review.db --city cities/yourcity
```

The editor desk runs at `http://127.0.0.1:8765` (`--port` picks another). `--city` turns on the
Sources and Models and auto-publish pages; without it the desk only reviews items.

An item is an **episode** (speakers, recap, podcast script) or an **article** (headline, dek and
sections of paragraphs, each with its citations; votes, quotes and topics come from the writer and
are not edited on the desk). Each item is in one state:

| State | Meaning | Exported |
|---|---|---|
| review | waiting for the editor | no |
| held | set aside | no |
| killed | will not run until it is edited or redrafted | no |
| published | ready to go out | yes |

The buttons on an item page:

- **Save** keeps your edits and sends the item back to review.
- **Publish** locks the item to its exact saved text. Any later change sends it back to review, and
  it must be published again. A killed item cannot be published until it is edited or redrafted.
- **Hold** and **Kill** take an item out of the export.
- **Redraft** sends your note to the writer and puts the new draft in review. The desk needs a
  writer connected for this; until the real writers are wired in, it says that no writer is
  connected.
- **Regenerate audio** (episodes only) records a request for new audio for the current text.

Each button shows what it is doing while it works, and each page says what happened. The item's
history lists every action.

**Auto-publish.** On the Models and auto-publish page, tick a body to let its items publish with no
press when they pass their checks. Items that fail their checks still wait for you. The setting is
saved to `cities/yourcity/desk.json`:

```json
{"format": 1, "auto_publish": {"City Council": false, "Planning and Zoning Commission": true}}
```

The weekly run stages items with `civiccast_brief.review.desk.stage_item(db, city_dir, kind=...,
meeting_id=..., title=..., meeting_date=..., body=..., checks_passed=...)`, which applies
auto-publish.

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

The default voice is Step-Audio-EditX (`stepaudio`). Add `--engine chatterbox` or `--engine dia2` to use another.

This writes `episode.mp3`, `chapters.json` and `transcript.vtt`.

## 7. Build the site

Turn each exported item into a site episode, saved in the city folder:

```
python -m civiccast_brief.episode assemble --item exported/<item> --meeting runs/2026-11-03 --script runs/2026-11-03/script.json --audio runs/2026-11-03/audio --media-base-url https://media.example.org --city yourcity --published 2026-11-04T09:00:00-07:00 --out cities/yourcity/2026-11-03.json
python -m civiccast_brief.site build --episodes cities/yourcity --out site --base-url https://yourcity.example.org
```

`assemble` refuses an item whose recap or script changed after publishing. The site build reads
`city.json` and every episode JSON in the folder (not `roster.json`) and publishes only approved
episodes. Upload `site/` to the static host and the MP3s to the media address.
