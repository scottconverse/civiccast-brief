"""The weekly run: every new meeting becomes a recap, a script, an episode and a story.

For each meeting :mod:`find` turned up, in date order:

0. the body's roster, filled from the meeting's own records (:mod:`rosterfill`): the roll call
   in the minutes and any member list on the agenda, by plain parsing, kept with their source;
1. ``agenda.txt`` from the portal's HTML agenda, when there is one;
2. ``meeting run`` (captions, speakers, recap) with the body's roster;
3. ``script write``; when the check fails, once more with the report, then ``script repair``;
4. the episode goes on the editor desk, published at once when its checks passed;
5. ``documents.json`` from the portal: the meeting's packet and its minutes (published, or the
   draft in a later packet); a document that is not there is a line in the report;
6. ``article skill`` (Scott's report-local-news skill run as an agent in its own folder) and
   ``article stage``, with ``documents.json`` when there is one.

With two or more meetings done in the window, a roundup follows: ``script roundup``, a script,
a written title, and its story built from the new meeting stories (``article from-stories``).

Then, when the site settings are given: ``review export``; for each published episode not built
yet, ``audio``, ``episode assemble`` (with the published story of the same meeting), the media
files copied under their episode names; each published story is written as a stand-alone
story file (so it is on the site with or without an episode); and ``site build``.

With ``Settings.worker`` (``--worker DIR``) a Windows PC does two of these steps for a cloud
server (see :mod:`civiccast_brief.weekly.worker`): a YouTube meeting waits until the PC sent its
captions, and a published episode waits until the PC sent its audio. Waiting is not a failure;
the PC's upload starts the next run or publish.

``weekly-state.json`` in the work folder records each meeting's steps, so a rerun picks up where
the last one stopped: a finished meeting is not run again, a failed one is tried again.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from civiccast_brief.audio.errors import ToolError
from civiccast_brief.audio.ffmpeg import probe_duration
from civiccast_brief.meeting.capture import LIVE_NOT_STARTED_REASON
from civiccast_brief.meeting.cli import LIVE_NOT_STARTED_EXIT
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.script.writer import (
    ROUNDUP_ID,
    ScriptFormatError,
    parse_txt,
    save_script,
)
from civiccast_brief.site.model import CITY_FILE, ROSTER_FILE, STORIES_DIR
from civiccast_brief.weekly import rosterfill
from civiccast_brief.weekly.documents import DOCUMENTS_NAME, write_documents
from civiccast_brief.weekly.find import Found
from civiccast_brief.weekly.worker import AUDIO, CAPTIONS, WorkerFolder, audio_key

Main = Callable[[Sequence[str]], int]
STATE_NAME = "weekly-state.json"
AUDIO_FILES = ("episode.mp3", "chapters.json", "transcript.vtt")
#: 64 kbps: an MP3's bytes should match its duration at about this rate. A half copy keeps the
#: header's full duration, so the ratio is the only sign the file is short.
MP3_BYTES_PER_SECOND = 8000


def audio_ready(audio: Path) -> bool:
    """True when a finished render is all there and its MP3 is whole.

    A render writes ``episode.mp3``, then ``chapters.json``, then ``transcript.vtt``, none of
    them atomically: a run stopped between them leaves a folder that must be rendered again, and
    a copy stopped halfway leaves an MP3 that still reports its full length."""
    if not all((audio / name).is_file() for name in AUDIO_FILES):
        return False
    mp3 = audio / "episode.mp3"
    try:
        duration = probe_duration(mp3)
    except ToolError:
        return False
    if duration <= 0:
        return False
    rate = mp3.stat().st_size / duration
    return 0.9 * MP3_BYTES_PER_SECOND <= rate <= 1.1 * MP3_BYTES_PER_SECOND


def _real(module: str) -> Main:
    def call(argv: Sequence[str]) -> int:
        import importlib

        name = "__main__" if module == "article" else "cli"
        main = importlib.import_module(f"civiccast_brief.{module}.{name}").main
        return int(main(list(argv)))

    return call


@dataclass
class Steps:
    """The command-line mains the run calls. Tests pass stand-ins."""

    meeting: Main = field(default_factory=lambda: _real("meeting"))
    script: Main = field(default_factory=lambda: _real("script"))
    episode: Main = field(default_factory=lambda: _real("episode"))
    article: Main = field(default_factory=lambda: _real("article"))
    review: Main = field(default_factory=lambda: _real("review"))
    audio: Main = field(default_factory=lambda: _real("audio"))
    site: Main = field(default_factory=lambda: _real("site"))


@dataclass(frozen=True)
class Site:
    """Where published episodes go. All of it, or no site step at all."""

    episodes: Path
    media: Path
    out: Path
    base_url: str
    media_base_url: str
    slug: str
    audio_engine: str = ""
    audio_fallback_engine: str = ""


@dataclass(frozen=True)
class Settings:
    city: Path
    work: Path
    db: Path
    diarize: bool = False
    site: Site | None = None
    #: The PC worker's folder: YouTube captions and audio come from a Windows PC.
    worker: Path | None = None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def folder_for(meeting: Found) -> str:
    return f"{meeting.day.isoformat()}-{slug(meeting.body)}-{slug(meeting.video)}"


def roster_for(city: Path, body: str) -> Path | None:
    """``rosters/<body>.json``, else ``roster.json`` when it is that body's roster."""
    own = city / "rosters" / f"{slug(body)}.json"
    if own.is_file():
        return own
    default = city / "roster.json"
    if default.is_file():
        data = json.loads(default.read_text(encoding="utf-8"))
        if " ".join(str(data.get("body", "")).lower().split()) == " ".join(body.lower().split()):
            return default
    return None


class State:
    """``weekly-state.json``: one entry per meeting key."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"format": 1, "meetings": {}, "roundups": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def meeting(self, key: str) -> dict[str, Any]:
        return self.data["meetings"].setdefault(key, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
        self.path.write_text(text, encoding="utf-8", newline="\n")


class Waiting(Exception):
    """A step waits for the PC worker; the meeting is tried again on the next run."""


class NotStarted(Exception):
    """The video is a live event or premiere that has not aired; try it on a later week."""


class StepFailed(Exception):
    """A step returned non-zero; the message names it."""


def _call(main: Main, argv: list[str], what: str) -> None:
    if main(argv) != 0:
        raise StepFailed(f"{what} failed")


def _script(steps: Steps, settings: Settings, folder: Path, body: str = "") -> tuple[Path, Path]:
    """Write the script; on a failed check, write once more with the report, then repair.

    Returns the spoken script and its JSON."""
    recap = str(folder / "recap.json")
    txt, js = folder / "script.txt", folder / "script.json"
    city = ["--city", str(settings.city)]
    roster = roster_for(settings.city, body) if body else None
    # A bare first name the claims carry ("Rob") prints as the roster's full name.
    roster_arg = ["--roster", str(roster)] if roster else []
    if steps.script(["write", "--recap", recap, "--out", str(txt), *city, *roster_arg]) == 0:
        return txt, js
    if not js.exists():
        raise StepFailed("script write failed (no script was written)")
    report = folder / "script-report.txt"
    steps.script(
        ["check", "--recap", recap, "--script", str(js), "--report", str(report), *roster_arg]
    )
    feedback = ["--feedback", str(report)] if report.exists() else []
    if (
        steps.script(["write", "--recap", recap, "--out", str(txt), *feedback, *city, *roster_arg])
        == 0
    ):
        return txt, js
    final = folder / "script-final.txt"
    _call(
        steps.script,
        ["repair", "--recap", recap, "--script", str(js), "--out", str(final), *roster_arg],
        "script repair",
    )
    return final, final.with_suffix(".json")


def _recap_facts(folder: Path) -> int:
    """How many facts (claims) the meeting's recap holds; 0 when it cannot be read."""
    try:
        recap = json.loads((folder / "recap.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    claims = recap.get("claims") if isinstance(recap, dict) else None
    return len(claims) if isinstance(claims, list) else 0


def _on_desk(settings: Settings, folder: Path, kind: str) -> bool:
    """True when the desk already has this item: a run that stopped after staging it and
    before saving its progress. Staging it again would fail with "already exists"."""
    if not Path(settings.db).is_file():
        return False
    recap = json.loads((folder / "recap.json").read_text(encoding="utf-8"))
    meeting_id = str(recap.get("video_id", ROUNDUP_ID))
    if meeting_id == ROUNDUP_ID:
        meeting_id = f"roundup {recap.get('meeting_date', '')}"
    with ReviewStore(settings.db) as store:
        return any(i.kind == kind and i.meeting_id == meeting_id for i in store.list_items())


def _stage_episode(steps: Steps, settings: Settings, folder: Path, script_json: Path) -> None:
    if _on_desk(settings, folder, "episode"):
        return
    _call(
        steps.episode,
        [
            "stage",
            "--db",
            str(settings.db),
            "--meeting",
            str(folder),
            "--script",
            str(script_json),
        ],
        "episode stage",
    )


def _article(steps: Steps, settings: Settings, folder: Path) -> str:
    """Write and stage the story. A story that fails its check is held in review on the desk
    (it is never published by itself); no story at all is a failure."""
    recap = str(folder / "recap.json")
    out = folder / "article"
    city = ["--city", str(settings.city)]
    documents = folder / DOCUMENTS_NAME
    docs = ["--documents", str(documents)] if documents.is_file() else []
    article = out.with_suffix(".json")
    if article.exists() and _on_desk(settings, folder, "article"):
        return str(article)
    article.unlink(missing_ok=True)
    # The story is written the TownReporter way: Scott's report-local-news skill, run
    # as an agent in its own folder, with the record supplied and no web search.
    steps.article(["skill", "--recap", recap, "--out", str(out), *city, *docs])
    if not article.exists():
        raise StepFailed("article skill failed (no story was written)")
    _call(
        steps.article,
        [
            "stage",
            "--db",
            str(settings.db),
            "--recap",
            recap,
            "--article",
            str(article),
            *city,
            *docs,
        ],
        "article stage",
    )
    return str(article)


def _fill_roster(settings: Settings, meeting: Found, folder: Path) -> list[str]:
    """Merge the members this meeting's records print into the body's roster. Never stops the
    meeting: a record that cannot be read is a line for the report."""
    try:
        path = roster_for(settings.city, meeting.body) or (
            settings.city / "rosters" / f"{slug(meeting.body)}.json"
        )
        documents = folder / DOCUMENTS_NAME
        data = json.loads(documents.read_text(encoding="utf-8")) if documents.is_file() else None
        agenda = meeting.agenda.text(meeting.day) if meeting.agenda is not None else ""
        return rosterfill.fill_roster(path, settings.city, meeting.body, meeting.day, data, agenda)
    except Exception as exc:  # noqa: BLE001 - a record that will not parse must not stop the meeting
        return [f"roster {meeting.body}: not filled from the records: {exc}"]


def run_meeting(steps: Steps, settings: Settings, meeting: Found, entry: dict[str, Any]) -> None:
    """Run one meeting's steps, skipping the ones ``entry`` says are done. Updates ``entry``."""
    folder = settings.work / folder_for(meeting)
    folder.mkdir(parents=True, exist_ok=True)
    entry.update(folder=str(folder), body=meeting.body, day=meeting.day.isoformat())
    done: list[str] = entry.setdefault("done", [])
    # The documents come first: the recap names a voice from a self-introduction only when
    # the packet, the minutes or the agenda print that name.
    if "documents" not in done and meeting.documents is not None:
        try:
            data, lines = meeting.documents()
        except Exception as exc:  # noqa: BLE001 - a portal or network failure must not stop the meeting
            # Not done: no file was written, so the next run tries the portal again and the
            # meeting is never handed a documents.json that does not exist.
            entry["documents"] = [f"documents not read: {exc}"]
        else:
            write_documents(folder, data)
            entry["documents"] = lines
            done.append("documents")
    if "roster" not in done:
        entry["roster"] = _fill_roster(settings, meeting, folder)
        done.append("roster")
    if "recap" not in done:
        roster = roster_for(settings.city, meeting.body)
        if roster is None:
            raise StepFailed(
                f"no roster for {meeting.body} (add rosters/{slug(meeting.body)}.json)"
            )
        direct = bool(meeting.media_url)
        youtube = meeting.video and not (meeting.granicus_host or meeting.cablecast_host or direct)
        if settings.worker is not None and youtube:
            inbox = WorkerFolder(settings.worker)
            if not inbox.copy_into(CAPTIONS, meeting.video, folder / "raw"):
                inbox.want_captions(meeting.video)
                raise Waiting("waiting for its captions from the PC worker")
        argv = ["run", "--out", str(folder), "--city", str(settings.city)]
        argv += ["--roster", str(roster)]
        if youtube:
            # The joined listed meeting is authoritative when the upload title has no day.
            argv += [
                "--meeting-date",
                f"{meeting.day:%B} {meeting.day.day}, {meeting.day.year}",
            ]
        if meeting.agenda is not None:
            agenda = folder / "agenda.txt"
            agenda.write_text(meeting.agenda.text(date.today()), encoding="utf-8", newline="\n")
            argv += ["--agenda", str(agenda)]
        documents = folder / DOCUMENTS_NAME
        if documents.is_file():
            argv += ["--documents", str(documents)]
        if meeting.granicus_host:
            argv += [
                "--granicus",
                meeting.granicus_host,
                "--view-id",
                str(meeting.granicus_view_id),
            ]
        if meeting.cablecast_host:
            argv += ["--cablecast", meeting.cablecast_host]
        if direct:
            # A CivicClerk, eScribe or Vimeo meeting: the source's own media and its caption
            # file when it lists one; without captions the run transcribes the audio.
            argv += [
                "--media-url",
                meeting.media_url,
                "--title",
                meeting.title,
                "--meeting-date",
                f"{meeting.day:%B} {meeting.day.day}, {meeting.day.year}",
            ]
            if meeting.captions_url:
                argv += ["--captions-url", meeting.captions_url]
        if settings.diarize:
            argv.append("--diarize")
        # argparse would read a leading-dash id as an option; -- keeps it positional.
        argv += ["--", meeting.video]
        code = steps.meeting(argv)
        if code == LIVE_NOT_STARTED_EXIT:
            # A scheduled stream or premiere that has not aired: nothing to report yet, so the
            # meeting is "not run" and a later week tries the video again.
            raise NotStarted(LIVE_NOT_STARTED_REASON)
        if code != 0:
            raise StepFailed("meeting run failed")
        done.append("recap")
    if "script" not in done and not _recap_facts(folder):
        # Boulder, Nov 15 2026: the recap failed in every window and the podcast was a
        # 24-second welcome and sign-off. With no facts there is no podcast yet. The story is
        # written from the record on its own, so it still runs; the recap runs again next time.
        if "article" not in done:
            entry["article"] = _article(steps, settings, folder)
            done.append("article")
        done.remove("recap")
        raise StepFailed(
            "the recap has no facts from the meeting; no podcast yet, recap retried next run"
        )
    if "script" not in done:
        # The meeting's own roster lets a bare first name in the claims print in full.
        txt, js = _script(steps, settings, folder, meeting.body)
        entry.update(script_txt=str(txt), script_json=str(js))
        done.append("script")
    if "episode" not in done:
        _stage_episode(steps, settings, folder, Path(entry["script_json"]))
        done.append("episode")
    if "article" not in done:
        entry["article"] = _article(steps, settings, folder)
        done.append("article")


def _record(state: State, entry: dict[str, Any], error: str) -> None:
    entry["status"] = "failed" if error else "done"
    entry["error"] = error
    state.save()


def run_roundup(steps: Steps, settings: Settings, state: State, last: date) -> str:
    """The roundup of the meetings done in the window that no earlier roundup covered. A meeting
    posted twice (two videos of one body on one day) counts once. Returns a line for the log."""
    key = last.isoformat()
    covered = {
        meeting
        for other, roundup in state.data["roundups"].items()
        if other != key
        for meeting in roundup.get("meetings", [])
    }
    meetings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for meeting_key, m in sorted(state.data["meetings"].items()):
        if m.get("status") != "done" or m.get("week") != key or meeting_key in covered:
            continue
        if (m.get("body", ""), m.get("day", "")) in seen:
            continue
        seen.add((m.get("body", ""), m.get("day", "")))
        meetings.append({**m, "key": meeting_key})
    if len(meetings) < 2:
        return f"roundup: not made ({len(meetings)} meeting(s) done this week; it needs 2)"
    entry = state.data["roundups"].setdefault(key, {})
    entry.setdefault("meetings", [m["key"] for m in meetings])
    done: list[str] = entry.setdefault("done", [])
    folder = settings.work / f"{key}-roundup"
    folder.mkdir(parents=True, exist_ok=True)
    entry["folder"] = str(folder)
    try:
        if "recap" not in done:
            argv = ["roundup", "--out", str(folder / "recap.json"), "--city", str(settings.city)]
            for m in sorted(meetings, key=lambda m: m["day"]):
                argv += ["--recap", str(Path(m["folder"]) / "recap.json")]
            _call(steps.script, argv, "script roundup")
            done.append("recap")
        if "script" not in done:
            # A roundup carries several bodies, so no single roster applies to its names.
            txt, js = _script(steps, settings, folder)
            entry.update(script_txt=str(txt), script_json=str(js))
            done.append("script")
        if "title" not in done:
            # No title that passes is not a failure: the episode keeps its dated title.
            recap = str(folder / "recap.json")
            steps.article(["title", "--recap", recap, "--city", str(settings.city)])
            done.append("title")
        if "article" not in done:
            recap = str(folder / "recap.json")
            out = folder / "article"
            if not _on_desk(settings, folder, "article"):
                out.with_suffix(".json").unlink(missing_ok=True)
                stories = [str(m["article"]) for m in meetings if m.get("article")]
                steps.article(
                    [
                        "from-stories",
                        "--recap",
                        recap,
                        "--out",
                        str(out),
                        *[part for story in stories for part in ("--story", story)],
                    ]
                )
                if not out.with_suffix(".json").exists():
                    raise StepFailed("roundup article write failed (no story was written)")
                _call(
                    steps.article,
                    [
                        "stage",
                        "--db",
                        str(settings.db),
                        "--recap",
                        recap,
                        "--article",
                        str(out.with_suffix(".json")),
                        "--city",
                        str(settings.city),
                    ],
                    "roundup article stage",
                )
            entry["article"] = str(out.with_suffix(".json"))
            done.append("article")
        if "episode" not in done:
            _stage_episode(steps, settings, folder, Path(entry["script_json"]))
            done.append("episode")
    except StepFailed as exc:
        entry.update(status="failed", error=str(exc))
        state.save()
        return f"roundup: failed: {exc}"
    entry.update(status="done", error="")
    state.save()
    return f"roundup: done ({len(meetings)} meetings)"


def _exported(folder: Path) -> list[tuple[Path, dict[str, Any]]]:
    items = []
    for item in sorted(p for p in folder.iterdir() if (p / "item.json").is_file()):
        items.append((item, json.loads((item / "item.json").read_text(encoding="utf-8"))))
    return items


def _run_folders(state: State) -> dict[str, dict[str, Any]]:
    """Meeting id -> the run entry, for every meeting and roundup that built an item.

    A meeting does not have to be wholly ``done``: the no-facts branch publishes the story and
    then marks the meeting failed, and that published story must still reach the site. Only an
    entry with a folder, a readable ``recap.json`` and a built item qualifies."""
    by_id: dict[str, dict[str, Any]] = {}
    for entry in [*state.data["meetings"].values(), *state.data["roundups"].values()]:
        folder = entry.get("folder") or ""
        done = entry.get("done", [])
        if not folder or ("article" not in done and "episode" not in done):
            continue
        recap_path = Path(folder) / "recap.json"
        if not recap_path.is_file():
            continue
        try:
            recap = json.loads(recap_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(recap, dict):
            continue
        transcript = recap_path.with_name("transcript.json")
        if transcript.is_file():
            # A failed meeting can leave a half-written transcript.json. It is skipped, not
            # allowed to end the publish before the site is built.
            try:
                meeting_id = str(json.loads(transcript.read_text(encoding="utf-8"))["video_id"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
        else:
            meeting_id = f"roundup {recap.get('meeting_date', '')}"
        by_id[meeting_id] = entry
    return by_id


def _desk_script(
    folder: Path, entry: dict[str, Any], item: Path, meta: dict[str, Any]
) -> tuple[Path, Path, Path]:
    """The script to speak, its JSON and the audio folder for a published episode.

    The desk's script is the one published. When a redraft or an edit changed it from the run's,
    it is written to its own folder and spoken from there, so the old audio is not used."""
    run_txt, run_json = Path(entry["script_txt"]), Path(entry["script_json"])
    desk_txt = item / "script.txt"
    if not desk_txt.is_file():
        return run_txt, run_json, folder / "audio"
    words = desk_txt.read_text(encoding="utf-8")
    if words.split() == run_txt.read_text(encoding="utf-8").split():
        return run_txt, run_json, folder / "audio"
    key = str(meta.get("content_hash") or hashlib.sha256(words.encode()).hexdigest())[:12]
    desk = folder / f"desk-{key}"
    desk.mkdir(exist_ok=True)
    try:
        lines = parse_txt(words)
    except ScriptFormatError as exc:
        raise StepFailed(f"the desk's script cannot be spoken: {exc}") from exc
    save_script(lines, desk / "script.txt", desk / "script.json")
    return desk / "script.txt", desk / "script.json", desk / "audio"


def _publish_stories(
    steps: Steps,
    site: Site,
    state: State,
    items: list[tuple[Path, dict[str, Any]]],
    runs: dict[str, dict[str, Any]],
    published: str,
) -> list[tuple[str, bool]]:
    """Write each published story as a stand-alone story file in ``<episodes>/stories``.

    A story is on the site whether or not its meeting has an episode: with an episode the site
    merges them, without one it is a page of its own. A story that is no longer published (held,
    killed or edited since) has its file removed, so it leaves the site on the next build.
    """
    log: list[tuple[str, bool]] = []
    folder = site.episodes / STORIES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    built_from: dict[str, str | None] = state.data.setdefault("stories_from", {})
    wanted: set[str] = set()
    for path, meta in items:
        if meta.get("kind") != "article" or meta.get("status") != "published":
            continue
        meeting_id = str(meta.get("meeting_id"))
        if meeting_id.startswith("roundup "):
            # A roundup has no meeting of its own: its story is rendered on the roundup page.
            continue
        name = f"{slug(meeting_id)}.json"
        wanted.add(name)
        out = folder / name
        if out.is_file() and built_from.get(meeting_id) == meta.get("content_hash"):
            log += _refresh_names(steps, out, runs.get(meeting_id), meeting_id)
            continue
        entry = runs.get(meeting_id)
        if entry is None:
            log.append(
                (f"{meeting_id}: story published, but no run of it is in this work folder", False)
            )
            continue
        argv = [
            "story",
            "--article", str(path),
            "--meeting", str(entry["folder"]),
            "--city", site.slug,
            "--published", published,
            "--out", str(out),
        ]  # fmt: skip
        try:
            _call(steps.episode, argv, "episode story")
        except StepFailed as exc:
            log.append((f"{meeting_id}: {exc}", True))
            continue
        built_from[meeting_id] = meta.get("content_hash")
        state.save()
        log.append((f"{meeting_id}: built story {name}", False))
    log += _drop_twice_published(folder, wanted, state.data.get("built", {}))
    for old in folder.glob("*.json"):
        if old.name not in wanted:
            old.unlink()
            log.append((f"{old.stem}: story is no longer published; removed from the site", False))
    return log


def _refresh_names(
    steps: Steps, site_file: Path, entry: dict[str, Any] | None, meeting_id: str
) -> list[tuple[str, bool]]:
    """Bring a built file's name fixes up to the current code and records. The file is built
    again only when its desk item changes, so without this a better name match never reached
    a meeting already on the site."""
    if entry is None:
        return []
    argv = ["names", "--file", str(site_file), "--meeting", str(entry["folder"])]
    try:
        _call(steps.episode, argv, "episode names")
    except StepFailed as exc:
        return [(f"{meeting_id}: {exc}", True)]
    return []


def _episode_video(path: Path) -> str:
    """The video a built episode file came from: its ``video_url`` (a YouTube watch link or the
    meeting's own MP4), else its ``media_url``. Empty when the file or address is missing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("video_url") or data.get("media_url") or "")


def _story_video(data: dict[str, Any]) -> str:
    """The video a built story file came from, the same address shape as an episode's."""
    return str(data.get("video_url") or data.get("media_url") or "")


def _drop_twice_published(
    folder: Path, wanted: set[str], built: dict[str, str]
) -> list[tuple[str, bool]]:
    """Keep one story per story id. Two published videos of one meeting (a city that uploads
    the same session twice) give the same id, and the site refuses a story id used twice, so
    one file is left off the site and the editor is told to kill one copy on the desk.

    The one kept is the story of the video the episode file on the site was built from: the
    episode and its story carry the same ``video_url`` (a YouTube link or the meeting's own
    MP4). The file is read after the episodes are written, so it is the episode this run left,
    or the one built in an earlier run. Without a matching address the state's ``built`` map is
    tried (meeting id -> episode name; an episode and its story share the id), and without an
    episode the first file by name is kept, as before."""
    log: list[tuple[str, bool]] = []
    by_id: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for name in sorted(wanted):
        path = folder / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            continue  # a missing or broken file is reported by the site build
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            continue
        by_id.setdefault(data["id"], []).append((name, data))
    for story_id, entries in by_id.items():
        names = [name for name, _ in entries]
        if len(names) < 2:
            continue
        keep = ""
        site_video = _episode_video(folder.parent / f"{story_id}.json")
        if site_video:
            keep = next((name for name, data in entries if _story_video(data) == site_video), "")
        if not keep:
            from_episode = {
                slug(meeting_id) for meeting_id, episode in built.items() if episode == story_id
            }
            keep = next((name for name in names if name.removesuffix(".json") in from_episode), "")
        keep = keep or names[0]
        for name in names:
            if name == keep:
                continue
            (folder / name).unlink()  # built again next run, and left off again until killed
            why = (
                f"{Path(name).stem}: same meeting as {Path(keep).stem} (story {story_id}); "
                "left off the site so it is not published twice. Kill one copy on the desk"
            )
            log.append((why, True))
    return log


def _drop_unpublished_episodes(
    site: Site, state: State, items: list[tuple[Path, dict[str, Any]]]
) -> list[tuple[str, bool]]:
    """Remove the files of each episode this run built whose item is no longer published
    (unpublished, held or killed on the desk), so it leaves the site on the next build. Files
    the run did not build are left alone."""
    built: dict[str, str] = state.data.setdefault("built", {})
    built_from: dict[str, str | None] = state.data.setdefault("built_from", {})
    published = {
        str(meta.get("meeting_id"))
        for _, meta in items
        if meta.get("kind") == "episode" and meta.get("status") == "published"
    }
    keep = {name for meeting_id, name in built.items() if meeting_id in published}
    log: list[tuple[str, bool]] = []
    for meeting_id in [m for m in built if m not in published]:
        name = built.pop(meeting_id)
        built_from.pop(meeting_id, None)
        if name in keep:
            # Two videos of one meeting share the name, and the file may hold the killed one's
            # episode: build the published one again over it.
            for other, other_name in built.items():
                if other_name == name:
                    built_from.pop(other, None)
            log.append((f"{meeting_id}: no longer published; episode {name} is built again", False))
            continue
        for path in (
            site.episodes / f"{name}.json",
            site.media / f"{name}.mp3",
            site.media / f"{name}-chapters.json",
            site.media / f"{name}.vtt",
        ):
            path.unlink(missing_ok=True)
        log.append((f"{meeting_id}: no longer published; episode {name} removed", False))
    state.save()
    return log


def publish(
    steps: Steps, settings: Settings, state: State, published: str
) -> list[tuple[str, bool]]:
    """Export the desk, build each published episode not built yet, and build the site.

    Returns the log lines, each with whether it is a failure."""
    site = settings.site
    assert site is not None
    log: list[tuple[str, bool]] = []
    exported = settings.work / "exported"
    _call(
        steps.review, ["export", "--db", str(settings.db), "--out", str(exported)], "review export"
    )
    items = _exported(exported)
    articles = {
        meta["meeting_id"]: path
        for path, meta in items
        if meta.get("kind") == "article" and meta.get("status") == "published"
    }
    runs = _run_folders(state)
    built: dict[str, str] = state.data.setdefault("built", {})
    #: The desk content each built episode was built from, so a redraft is built again.
    built_from: dict[str, str | None] = state.data.setdefault("built_from", {})
    site.episodes.mkdir(parents=True, exist_ok=True)
    site.media.mkdir(parents=True, exist_ok=True)
    # The site build reads city.json from the episodes folder, so the run keeps the city
    # folder's copy there and an edit to the city settings reaches the site on the next build.
    city_file = settings.city / CITY_FILE
    if city_file.is_file():
        shutil.copyfile(city_file, site.episodes / CITY_FILE)
    log += _drop_unpublished_episodes(site, state, items)
    for path, meta in items:
        meeting_id = str(meta.get("meeting_id"))
        if meta.get("kind") != "episode":
            continue
        if meeting_id in built and built_from.get(meeting_id) == meta.get("content_hash"):
            built_file = site.episodes / f"{built[meeting_id]}.json"
            if built_file.is_file():
                log += _refresh_names(steps, built_file, runs.get(meeting_id), meeting_id)
            continue
        entry = runs.get(meeting_id)
        if entry is None:
            log.append((f"{meeting_id}: published, but no run of it is in this work folder", False))
            continue
        folder = Path(entry["folder"])
        try:
            script_txt, script_json, audio = _desk_script(folder, entry, path, meta)
            if not audio_ready(audio):
                if settings.worker is not None:
                    # The worker's .done marker means its three files are a finished render;
                    # the cloud does not probe or re-render what the PC sent.
                    words = script_txt.read_text(encoding="utf-8")
                    key = audio_key(words, site.audio_engine)
                    inbox = WorkerFolder(settings.worker)
                    if not inbox.copy_into(AUDIO, key, audio):
                        inbox.want_audio(key, words, site.audio_engine)
                        log.append(
                            (f"{meeting_id}: waiting for its audio from the PC worker", False)
                        )
                        continue
                else:
                    engine = ["--engine", site.audio_engine] if site.audio_engine else []
                    if site.audio_fallback_engine:
                        engine += ["--fallback-engine", site.audio_fallback_engine]
                    _call(steps.audio, [str(script_txt), "--out", str(audio), *engine], "audio")
            out = folder / "episode.json"
            argv = [
                "assemble",
                "--item", str(path),
                "--meeting", str(folder),
                "--script", str(script_json),
                "--audio", str(audio),
                "--media-base-url", site.media_base_url,
                "--city", site.slug,
                "--published", published,
                "--out", str(out),
            ]  # fmt: skip
            if meeting_id in articles:
                argv += ["--article", str(articles[meeting_id])]
            _call(steps.episode, argv, "episode assemble")
            episode = json.loads(out.read_text(encoding="utf-8"))
            name = episode["id"]
            shutil.copyfile(out, site.episodes / f"{name}.json")
            for source, target in (
                ("episode.mp3", f"{name}.mp3"),
                ("chapters.json", f"{name}-chapters.json"),
                ("transcript.vtt", f"{name}.vtt"),
            ):
                shutil.copyfile(audio / source, site.media / target)
        except (StepFailed, OSError) as exc:
            # One item's bad audio or missing file is logged; the other items and the site
            # build still run.
            log.append((f"{meeting_id}: {exc}", True))
            continue
        built[meeting_id] = name
        built_from[meeting_id] = meta.get("content_hash")
        state.save()
        log.append((f"{meeting_id}: built episode {name}", False))
    log.extend(_publish_stories(steps, site, state, items, runs, published))
    has_stories = any((site.episodes / STORIES_DIR).glob("*.json"))
    if not has_stories and not any(
        p.name not in (CITY_FILE, ROSTER_FILE) for p in site.episodes.glob("*.json")
    ):
        # Nothing approved yet is the desk doing its job, not a failed build.
        log.append(
            ("site: not built: no episode is published yet; approve items on the desk", False)
        )
        return log
    argv = ["build", "--episodes", str(site.episodes), "--out", str(site.out), "--stills"]
    _call(steps.site, [*argv, "--base-url", site.base_url], "site build")
    log.append((f"site: built into {site.out}", False))
    return log


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    failed: int = 0

    def add(self, line: str, failed: bool = False) -> None:
        self.lines.append(line)
        self.failed += int(failed)


def run_week(
    settings: Settings,
    meetings: Sequence[Found],
    last: date,
    steps: Steps | None = None,
    published: str | None = None,
) -> Report:
    """Run every meeting not done yet, the roundup, and the site. One meeting's failure is
    recorded and the run goes on with the next."""
    steps = steps or Steps()
    state = State(settings.work / STATE_NAME)
    report = Report()
    for meeting in meetings:
        entry = state.meeting(meeting.key)
        entry.update(title=meeting.title, week=last.isoformat())
        # A meeting run before keeps its run, even when it would not be run now: its episode
        # and story are on the desk and are built from that run's folder.
        if entry.get("status") == "done":
            report.add(f"{meeting.day} {meeting.title}: done in an earlier run")
            continue
        if meeting.reason:
            entry.update(status="not run", error=meeting.reason)
            state.save()
            report.add(f"{meeting.day} {meeting.title}: not run: {meeting.reason}")
            continue
        try:
            run_meeting(steps, settings, meeting, entry)
        except Waiting as exc:
            entry.update(status="waiting", error=str(exc))
            state.save()
            report.add(f"{meeting.day} {meeting.title}: {exc}")
            continue
        except NotStarted as exc:
            entry.update(status="not run", error=str(exc))
            state.save()
            report.add(f"{meeting.day} {meeting.title}: not run: {exc}")
            continue
        except StepFailed as exc:
            _record(state, entry, str(exc))
            report.add(f"{meeting.day} {meeting.title}: failed: {exc}", failed=True)
            for line in entry.get("roster", []):
                report.add(f"{meeting.day} {meeting.title}: {line}")
            continue
        _record(state, entry, "")
        report.add(f"{meeting.day} {meeting.title}: done")
        for line in [*entry.get("documents", []), *entry.get("roster", [])]:
            report.add(f"{meeting.day} {meeting.title}: {line}")
    line = run_roundup(steps, settings, state, last)
    report.add(line, failed=line.startswith("roundup: failed"))
    if settings.site is not None:
        _publish_into(report, steps, settings, state, published)
    return report


def publish_site(
    settings: Settings, steps: Steps | None = None, published: str | None = None
) -> Report:
    """Only the site steps: export the desk, build what is published, build the site.

    The station runs this about half a minute after an editor publishes, edits or kills a
    public item, so the site follows the desk without waiting for the weekly run."""
    assert settings.site is not None, "publish_site needs the site settings"
    report = Report()
    _publish_into(report, steps or Steps(), settings, State(settings.work / STATE_NAME), published)
    return report


def _publish_into(
    report: Report, steps: Steps, settings: Settings, state: State, published: str | None
) -> None:
    when = published or datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        for line, failed in publish(steps, settings, state, when):
            report.add(line, failed=failed)
    except StepFailed as exc:
        report.add(f"site: {exc}", failed=True)
