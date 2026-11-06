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
| TownRecord in its own venv | YouTube capture | Set `CIVICCAST_TOWNRECORD_PYTHON` to that venv's `python.exe`. Not needed for a Granicus city. |
| textflowkit in its own venv | `--diarize` only | Set `CIVICCAST_TEXTFLOWKIT_PYTHON`. Without `--diarize`, names come from the roll call only. |
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

For a Granicus city, add `--granicus yourcity.granicus.com --view-id 2`. Use `--body` when the
meetings are not a "City Council" (for example `--body "Board of Supervisors"`).

This writes two files and never overwrites one that is already there:

- `cities/yourcity/city.json`: the site, podcast and capture settings. Edit the `about` text, the
  host names and the podcast description here.
- `cities/yourcity/roster.json`: the council roster, with no members yet.

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

## 5. Review and approve

```
python -m civiccast_brief.episode stage --db review.db --meeting runs/2026-11-03 --script runs/2026-11-03/script.json
python -m civiccast_brief.review serve --db review.db
```

The producer screen runs at `http://127.0.0.1:8765`. Name the unknown voices, read the recap and the
script, then approve or reject. Approval is locked to the exact text; any later change needs a new
approval. Then export the approved items:

```
python -m civiccast_brief.review export --db review.db --out exported
```

## 6. Make the audio

```
python -m civiccast_brief.audio runs/2026-11-03/script.txt --engine chatterbox --out runs/2026-11-03/audio
```

This writes `episode.mp3`, `chapters.json` and `transcript.vtt`.

## 7. Build the site

Turn each approved item into a site episode, saved in the city folder:

```
python -m civiccast_brief.episode assemble --item exported/<item> --meeting runs/2026-11-03 --script runs/2026-11-03/script.json --audio runs/2026-11-03/audio --media-base-url https://media.example.org --city yourcity --published 2026-11-04T09:00:00-07:00 --out cities/yourcity/2026-11-03.json
python -m civiccast_brief.site build --episodes cities/yourcity --out site --base-url https://yourcity.example.org
```

`assemble` refuses an item whose recap or script changed after approval. The site build reads
`city.json` and every episode JSON in the folder (not `roster.json`) and publishes only approved
episodes. Upload `site/` to the static host and the MP3s to the media address.
