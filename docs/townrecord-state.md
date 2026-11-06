# TownRecord state (Phase 0 note)

Checked 2026-11-05 for PLAN.md sections 4 and 5. Two trees were checked:

- **Local checkout** `C:\Users\scott\Desktop\Code\townrecord`, branch `main` at `11382a1` (Merge PR #20, Sep 27 2026). Tests and the one capture ran here.
- **origin/main** at `1b82b42` (Merge PR #29). It is 37 commits ahead of local main: `git rev-list --count main..origin/main` gives 37, and `git rev-list --count origin/main..main` gives 0. It was tested from an extract made with `git archive origin/main core`, so the checkout was not touched.

No file in the TownRecord repo was changed. Scratch output is in `C:\dev\civiccast-brief-scratch\townrecord-capture\`.

## What works (shown)

- **Test suite, local main: 1113 passed, 2 skipped, 1 warning, 423.45 s, no failures.** Command: `python -m pytest -p no:cacheprovider -q` in a venv built by `uv sync --locked --python 3.12` (Python 3.12.10, pytest 9.1.1). Log: `pytest-full.log`.
- **The 2 skips are Windows-only.** `core/tests/capture/test_command.py:109` ("a backslash separates on Windows") and `core/tests/test_storage.py:68` ("chmod does not make a folder unwritable on Windows"). A targeted run with `-rs` confirmed both: 24 passed, 2 skipped.
- **The 1 warning, verbatim:** `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` It comes from a dependency (`fastapi/testclient.py`).
- **Test suite, origin/main: 1466 passed, 41 skipped, 1 warning in 619.07 s, exit 0.** Same command, run in an extract (`git archive origin/main core`). Log: `pytest-origin-main.log`. Of the 41 skips, 39 are fixtures that were absent (37 "recorded fixture not present", 2 "was not found"). Those tests read fixtures from a sibling folder named `townrecord-oversight` (`core/tests/records/conftest.py:61` resolves `parents[4] / "townrecord-oversight" / "evidence" / "fixtures"`). The extract does not have that folder, so the assertions in those 39 tests did not run here. The other 2 are the Windows-only skips seen on local main. The warning is the same StarletteDeprecationWarning.
- **Lint, local main:** `ruff check --no-cache` reports "All checks passed!" and `ruff format --check --no-cache` reports "187 files already formatted". These are the lint steps in `.github/workflows/ci.yml`.
- **Imports.** All 17 modules the plan names import on local main. The one exception is `townrecord.ai.grounding`, which fails with `ModuleNotFoundError: No module named 'townrecord.ai'` (`local-import-check.txt`).
- **One Longmont capture ran through the real job path.** Driver: `script/one_capture.py`, run with `python -I`. It created a scratch database and an accepted source for the City of Longmont channel `UCH5_wkpLrKYb1JuUk6-UdNg` (`core/tests/video/conftest.py:26`), then one video row for "City Council Regular Session - 20 October 2026" (YouTube id `uHFuCfd5MVQ`). It enqueued one `capture_captions` job and ran `Runner.run_once` (`core/src/townrecord/jobs/runner.py:186`).
  - Job `state = done`, `last_error = None`, about 8 seconds (`capture-run.log`).
  - Video `capture_state = captions`.
  - One transcript, origin `auto_captions`, `is_provisional = 1`. These are YouTube's automatic captions, not the publisher's.
  - 8,032 segments spanning 36.0 s to 20,097 s. Zero of them have a speaker label.
  - The text is real public-comment speech. Captions just after the one-hour mark include "Thank you, mayor, city council." (queried from the scratch database; the speaker's name is left out here).
  - Stored files: info.json 649,381 bytes, caption file (srv3) 1,965,186 bytes.
- **yt-dlp reads the channel.** `yt-dlp --flat-playlist --dump-single-json --playlist-end 40 --js-runtimes node .../UCH5.../streams` (yt-dlp 2026.08.19) returned 40 entries, including the Oct 20 regular session (`city-streams.json`).
- **The channel page answers.** `curl` of the City channel `/streams` page returned HTTP 200.
- **CLI.** `townrecord status` runs against the scratch database and exits 0 (`status-cli.txt`). It shows the accepted source and the done job.

## What does not work or is missing

1. **The local checkout is stale, and the plan points at code that exists only on GitHub.** Local main is an ancestor of origin/main, 37 commits behind. The `ai/` package (including `grounding.py`) exists only on origin/main and on the two deepseek branches. Capture changed upstream: `git diff --stat main origin/main -- core/src/townrecord/{capture,video,adapters,service.py,ai}` shows 22 files and 5,368 insertions. origin/main adds `capture/recheck.py`, `capture/settle.py` and `capture/requests.py`.
2. **The README is wrong, and the plan's "~30,800 lines" depends on which tree you count.** `README.md:11` says "Pre-alpha. Specification stage. No working code yet." The core is a working Python service with tests. Counts from `find ... | xargs wc -l`:
   - local main: `core/src` Python 23,808; `core/src` SQL 1,173; `core/tests` Python 23,135.
   - origin/main: `core/src` Python 30,822; `core/src` SQL 1,438; `core/tests` Python 29,543.
   The plan's figure matches origin/main's `core/src` Python. What is still missing is the desktop shell: the repo has no Tauri, `src-tauri`, `package.json` or `.rs` files (`git ls-files`). `docs/SPEC.md:5` still says "Status: draft".
3. **A fresh install cannot get the yt-dlp runtime.** The CLI has only `serve`, `token` and `status` (`core/src/townrecord/cli.py:94-113`). Two messages tell the user to "Run setup" (`runtime/manager.py:227`, `capture/transcribe.py:354`), but no setup command exists. The only installer is the daily `runtime_update` job (`schedule.py:80`; `runtime/manager.py:411` and `:435`). It installs the newest yt-dlp and runs the known-video probe. If the probe raises or fails, the new version is not activated (`manager.py:475-484`). With `TOWNRECORD_TEST_VIDEO` unset, the probe raises `NoTestVideo` (`capture/probe.py:47`). The schedule runs only inside `serve` (`service.py:116`) and needs a time zone (`schedule.py:89`). For my test I pip-installed yt-dlp 2026.8.19 into the scratch venv, which is why `status` still says "yt-dlp: not installed" (`status-cli.txt`). That was a test bypass, not the product path.
4. **YouTube's RSS feed returned 404 from this machine.** `curl .../feeds/videos.xml?channel_id=UCH5_wkpLrKYb1JuUk6-UdNg` returned 404 ("Error 404 (Not Found)!!1"). A control channel, `UC_x5XG1OV2P6uZZ5FSM9Ttw` (Google Developers), also returned 404. The ladder's second step is this feed (`video/youtube/ladder.py`), so it would fall through to yt-dlp. The cause is unknown: a YouTube change or this network. I checked once.
5. **One listing step is not built.** `ladder.py:3` says "Four ways, two of which are built here plus one more in a later unit". `ladder.py:115-116` is a `TODO(unit D)` for the channel-tab HTML step. The code has three steps: Data API (needs a key), RSS, and yt-dlp. The same TODO is on origin/main at the same lines.
6. **The HTTP API is read-only.** Every route in `core/src/townrecord/api/` is a GET (`api/app.py:61-77`, `api/read.py:454` onward). A grep for post, put, delete and patch found nothing. Sources, jobs and captures can only be created by code or by writing the database directly.
7. **The watch-to-capture path was not run, on purpose.** `video/watch.py:348` `watch_channel` enqueues a capture for every meeting in a channel listing. For one capture I wrote the video row by hand and enqueued only the capture job. So the watch path is not proven.
8. **CI has no docs-only filter.** `.github/workflows/ci.yml` lines 3-5 trigger on every push and every pull request. The file is identical on origin/main (blob `6ed749d`). That breaks the CI RULE in CLAUDE.md, so any reuse of this workflow needs the filter first.
9. **The captions are provisional and unlabelled.** The transcript is `auto_captions` with `is_provisional = 1`, and no segment has a speaker label. Speaker naming and diarization are new work, as the plan says (section 4).
10. **Audio fallback not exercised.** `core/src/townrecord/stt/textflowkit.py` exists, but TextFlowKit is not installed, so the audio path did not run.

## What we can reuse and how (entry points)

Paths are under `core/src/townrecord/` unless stated. Tests are under `core/tests/`. Pacing is not in `capture`, `video` or `adapters`: it is the top-level `pacing.py`, so plan line 71 is slightly off.

- **Caption command.** `capture/command.py:108` `capture_argv(interpreter, work_dir, archive, url, js_runtime=...)` builds the yt-dlp command. `:162` `run_capture(runner, argv, timeout_s=...)` runs it. Tests: `tests/capture`, 94 collected.
- **Caption job.** `capture/job.py:152` `CaptionCapture` handles `capture_captions`. Its payload is a `videos.id` (`job.py:123` `video_id_from`). Register it with `capture/__init__.py:56` `register(storage_root=..., registry=..., runner=..., interpreter=...)`.
- **Parsing and storage.** `capture/job.py:139` `parse_caption(path)`. `capture/store.py:67` `insert_transcript_with_segments(...)` is the one transcript write path. The package docstring in `capture/__init__.py` says both captions and audio go through it.
- **Listing.** `video/youtube/ladder.py:67` `ListingLadder(data_api=, rss=, ytdlp=)`, called as `.list_videos(channel_id=..., channel_url=...)`. Readers: `rss.py:53` `RssLister`, `ytdlp.py:93` `YtdlpFlatLister`, `data_api.py:164` `DataApiClient` (needs a key; none was used).
- **Channel watch.** `video/watch.py:333` `register(...)`, `:348` `watch_channel(ctx, ladder)`, `:206` `channel_of(origin)` (reads a channel URL, handle or id).
- **Readiness.** `video/youtube/readiness.py:134` `readiness(info=...)`. `video/youtube/status.py:188` `StatusAsker` asks for status when readiness is unknown.
- **Meeting filter.** `video/youtube/classify.py:95` `MeetingClassifier`.
- **Pacing.** `pacing.py:162` `PaceSettings`, `:241` `Pacer` (`.wait(what=...)`), `:469` `pacer()`. `service.py:209` installs one pace per process.
- **Known-video test.** `capture/probe.py:57` `capture_probe(test_video_url=...)`.
- **PrimeGov.** `adapters/primegov.py:258` `PrimeGovAdapter`, against the protocol `adapters/base.py:130` `RecordsAdapter`. Tests: `tests/adapters`, 42 collected. Not run live.
- **Wiring and one run.** `service.py:144` `build_service(settings)`. `jobs/runner.py:186` `Runner.run_once(lane)`. `jobs/queue.py:168` `enqueue(conn, kind, payload, lane)`. Rows: `repo/area.py:27` `insert_jurisdiction`, `:284` `insert_source`; `repo/meetings.py:160` `insert_video`.
- **Grounding (origin/main only).** In `ai/grounding.py` on origin/main: `evidence_pack` (line 689), `check_sentence` (859), `check_answer` (1058), `ground(answer, pack, ask=None)` (1196). The module docstring says "Nothing here calls a model." Tests: `tests/ai/test_grounding.py`, origin/main only: 67 passed, 6 skipped (fixture absent, same sibling-folder cause), 1 warning, exit 0 (`pytest -rs tests/ai/test_grounding.py`).

## Not checked

- Live PrimeGov and Granicus capture.
- The watch job against live YouTube, and the ladder's fallback order in a real run.
- HTTP 429 handling. No 429 was seen, so the pacer's hold was not exercised.
- The YouTube Data API step (needs a key; none used).
- Installing yt-dlp through the product's runtime job. That needs `TOWNRECORD_TEST_VIDEO` and a time zone, and neither was set.
- The TextFlowKit audio fallback (no audio was downloaded).
- `townrecord serve` and the API routes (not started).
- A capture on origin/main. The one capture ran on local main only.
- Transcript quality beyond three sampled windows, and speaker data (none exists).
- Speaker naming, diarization and grounding on real output.
- macOS and Linux CI jobs (Windows only).
- Whether the RSS 404 persists (checked once, Nov 5).
- The 39 origin/main tests skipped for absent fixtures (the sibling `townrecord-oversight` folder), and the 6 grounding tests skipped for the same reason. Their assertions did not run.

## Housekeeping

- My capture run wrote 94 gitignored `.pyc` files into the TownRecord repo, because `python -I` ignores `PYTHONDONTWRITEBYTECODE`. I deleted the 94 files whose timestamps are from today. The 41 older `.pyc` files and `core/.venv` are untouched. `git status --short` is empty.
- The scratch venvs are `C:\dev\civiccast-brief-scratch\townrecord-venv` (local) and `townrecord-origin-venv` (origin/main).
