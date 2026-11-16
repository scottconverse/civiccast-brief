# Transcribe direct-media meetings that have no captions

Run folder: `C:\dev\civiccast-brief-transcribe` (worktree, branch `transcribe-direct-media`,
based on `origin/main` 5f168c7 / PR #93). Date: 2026-11-16.

## Plan (written before the change)

### What the code does now

- `meeting/direct.py:capture()` needs `captions_url`. A direct-media meeting (CivicClerk,
  eScribe, Vimeo) with only `media_url` raises `DirectError` ("does not transcribe audio").
- `weekly/find.py:_video_source()` marks that meeting `NO_CAPTIONS`, so
  `weekly/run.py:run_meeting()` records it as "not run" and never calls `meeting run`.
- The project already has a local speech path: `record/stt/textflowkit.py`
  (`speech_command()`, `textflowkit_argv()`, `transcription_timeout_s()`,
  `parse_textflowkit_json()`) runs `python -m civiccast_brief.speech transcribe` (Whistle, on
  this machine, no hosted model) and reads its JSON into the shared caption `Segment` type.
  `meeting/direct.py` already has `fetch_audio()`, which makes 16 kHz mono WAV with ffmpeg
  (`-vn`, so video is not downloaded).
- The Granicus/Cablecast weekly readers themselves still stop on missing captions; the
  existing *transcription* path they share is the one above (also used by the `transcribe`
  job). Nothing new is installed or added.

### Smallest change

1. `meeting/direct.py`
   - `capture()`: when `captions_url` is empty and `media_url` is present, call a new
     `transcribe()`; when both are empty, raise `DirectError` (no media).
   - `transcribe()`: reuse `fetch_audio()` for the audio, then the existing
     `speech_command()` + `textflowkit_argv()` + `transcription_timeout_s()` +
     `parse_textflowkit_json()` to make caption items. Save/reuse the transcript JSON in
     `raw/transcript/`. A failed or empty transcription is a `DirectError` with its reason.
   - `DirectCapture` gains `transcribed: bool` so the pipeline can name the real source.
2. `meeting/pipeline.py`: label the capture `local_speech_to_text` when it was transcribed,
   and describe it as this machine's speech program; `supplied_captions` otherwise.
3. `weekly/find.py`: drop the `NO_CAPTIONS` skip. A meeting with `media_url` and no
   `captions_url` is now runnable; keep `NO_MEDIA` when `media_url` is missing too.
4. `weekly/run.py` already omits `--captions-url` when there is none, so no change is needed
   beyond the docstring.
5. Tests (fakes only; no network, no GPU, no real transcription):
   - `tests/meeting/test_direct.py` (new): transcript path, reuse, empty transcript, failure,
     and that `capture()` dispatches on `captions_url`.
   - `tests/weekly/test_weekly_video_sources.py`: the no-caption listings are now runnable,
     and the run command omits `--captions-url`.

### City config (outside the repo)

`C:\dev\civiccast-brief-scratch\weekly-superior\cities\superior\sources.json`: add
`civicclerk:superiorco` with body `Town Council` (the name `city.json` and every 2026 title
use for the Trustees), keep the YouTube sources, remove the stale Granicus `view_id=13`.

### Live check

`find` only (no transcription), Superior and Erie, last 60 days: date, body, title, media
host, captions yes/no, and whether the run step would now run it.

## Results

### Code changed

- `src/civiccast_brief/meeting/direct.py`
  - `capture()` now dispatches: a listed `captions_url` is read exactly as before; a meeting
    with `media_url` and no captions calls the new `transcribe()`; with neither it raises
    `DirectError(NO_MEDIA)`.
  - `transcribe()` reuses `fetch_audio()` (ffmpeg, `-vn`, 16 kHz mono WAV) and the existing
    local speech helpers from `record/stt/textflowkit.py` (`speech_command`,
    `textflowkit_argv`, `transcription_timeout_s`, `parse_textflowkit_json`). It saves the
    transcript JSON under `raw/transcript/`, reuses it on a later run, and reports a failed or
    empty transcription as a `DirectError` with the reason. No new engine, dependency or
    hosted model.
  - `DirectCapture` gained `transcribed: bool`.
  - An empty *source caption file* is still an error, now worded accurately.
- `src/civiccast_brief/meeting/pipeline.py`: a transcribed direct capture is recorded with
  `caption_kind: local_speech_to_text` and says the lines came from this machine's speech
  program; a captioned one stays `supplied_captions`.
- `src/civiccast_brief/weekly/find.py`: the `NO_CAPTIONS` skip is gone. A direct-media meeting
  with `media_url` is runnable whether or not it lists captions; `NO_MEDIA` is kept for a
  meeting with no media address. Module docstring updated.
- `src/civiccast_brief/weekly/run.py`: comment updated only; the command already omits
  `--captions-url` when the meeting has none.

### Tests (fakes only: no network, GPU or real transcription)

- New `tests/meeting/test_direct.py` (6 tests): transcribes from media, reuses a saved
  transcript without running the program, empty transcript fails, failed program fails, and
  `capture()` dispatches between captions, transcription and no media.
- `tests/weekly/test_weekly_video_sources.py`: the CivicClerk Council event and the Vimeo
  study session with no captions are now `reason == ""`; a new test checks the run command
  passes `--media-url` and no `--captions-url`.

`uv run pytest tests/meeting/test_direct.py tests/weekly/test_weekly_video_sources.py -q`:
**17 passed**.

`uv run ruff check` and `uv run ruff format --check` on the six changed/new files:
**All checks passed; all files formatted.**

### City config (outside the repo)

`C:\dev\civiccast-brief-scratch\weekly-superior\cities\superior\sources.json`:
added `civicclerk:superiorco` (tenant `superiorco`, body `Town Council` — the name the 2026
CivicClerk titles and `city.json` use for the Board of Trustees), kept both YouTube sources,
removed the stale Granicus `view_id=13` source.

### Live find check (find only; no transcription), 2026-09-18..2026-11-16

Run through `weekly.__main__.real_listers` + `find_meetings`; nothing was executed.

Superior:

| Date | Body | Title | Media host | Captions | Run step would run? |
|---|---|---|---|---|---|
| 2026-09-28 | Town Council | Town Council Meeting | cpmedia.azureedge.net | no | **yes** (transcribe) |
| 2026-10-12 | Town Council | Town Council Meeting | cpmedia.azureedge.net | no | **yes** (transcribe) |
| 2026-10-26 | Town Council | Town Council Meeting | cpmedia.azureedge.net | no | **yes** (transcribe) |
| 2026-10-16 | (none) | Historical Commission (HC) 10/16/26 | youtube | no | no — title names no covered body |
| 2026-10-18 | (none) | Historical Commission (HC) 10/18/26 | youtube | no | no — title names no covered body |
| 2026-11-20 | (none) | Historical Commission (HC) 11/20/26 | youtube | no | no — title names no covered body (its day is also after the window) |

The Sept 14 Town Council meeting from the earlier survey falls outside this 60-day window.
The Oct 12/26 Town Council YouTube uploads were not listed by the channel reader in this run
(its feed returns only the newest entries); the CivicClerk meetings cover those dates.

Erie (all CivicClerk, all `cpmedia.azureedge.net`, all captions=no, all **run=yes** by
transcription):

| Date | Body | Title |
|---|---|---|
| 2026-09-22 | Town Council | Town Council Meeting |
| 2026-09-22 | Urban Renewal Authority | Urban Renewal Authority |
| 2026-09-29 | Town Council | Town Council Special Meeting |
| 2026-10-06 | Town Council | Town Council Special Meeting |
| 2026-10-07 | Planning Commission | Planning Commission Meeting |
| 2026-10-13 | Town Council | Town Council Meeting |
| 2026-10-20 | Town Council | Town Council Special Meeting |
| 2026-10-21 | Planning Commission | Planning Commission and Open Space and Trails Advisory Board Joint Study Session |
| 2026-10-27 | Town Council | Town Council Meeting |
| 2026-11-10 | Town Council | Town Council Meeting |
| 2026-11-10 | Urban Renewal Authority | Urban Renewal Authority |

No source problems were reported for either city.

### PR

https://github.com/scottconverse/civiccast-brief/pull/94
