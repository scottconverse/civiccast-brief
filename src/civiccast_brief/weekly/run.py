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
6. ``article write`` and ``article stage``, with ``documents.json`` when there is one.

With two or more meetings done in the window, a roundup follows: ``script roundup``, a script,
a written title, and its episode on the desk.

Then, when the site settings are given: ``review export``; for each published episode not built
yet, ``audio``, ``episode assemble`` (with the published story of the same meeting), the media
files copied under their episode names; each published story is written as a stand-alone
story file (so it is on the site with or without an episode); and ``site build``.

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

Main = Callable[[Sequence[str]], int]
STATE_NAME = "weekly-state.json"


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


@dataclass(frozen=True)
class Settings:
    city: Path
    work: Path
    db: Path
    diarize: bool = False
    site: Site | None = None


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


class StepFailed(Exception):
    """A step returned non-zero; the message names it."""


def _call(main: Main, argv: list[str], what: str) -> None:
    if main(argv) != 0:
        raise StepFailed(f"{what} failed")


def _script(steps: Steps, settings: Settings, folder: Path) -> tuple[Path, Path]:
    """Write the script; on a failed check, write once more with the report, then repair.

    Returns the spoken script and its JSON."""
    recap = str(folder / "recap.json")
    txt, js = folder / "script.txt", folder / "script.json"
    city = ["--city", str(settings.city)]
    if steps.script(["write", "--recap", recap, "--out", str(txt), *city]) == 0:
        return txt, js
    if not js.exists():
        raise StepFailed("script write failed (no script was written)")
    report = folder / "script-report.txt"
    steps.script(["check", "--recap", recap, "--script", str(js), "--report", str(report)])
    feedback = ["--feedback", str(report)] if report.exists() else []
    if steps.script(["write", "--recap", recap, "--out", str(txt), *feedback, *city]) == 0:
        return txt, js
    final = folder / "script-final.txt"
    _call(
        steps.script,
        ["repair", "--recap", recap, "--script", str(js), "--out", str(final)],
        "script repair",
    )
    return final, final.with_suffix(".json")


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
    steps.article(["write", "--recap", recap, "--out", str(out), *city, *docs])
    if not article.exists():
        raise StepFailed("article write failed (no story was written)")
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
        argv = ["run", meeting.video, "--out", str(folder), "--city", str(settings.city)]
        argv += ["--roster", str(roster)]
        if meeting.agenda is not None:
            agenda = folder / "agenda.txt"
            agenda.write_text(meeting.agenda.text(date.today()), encoding="utf-8", newline="\n")
            argv += ["--agenda", str(agenda)]
        if "documents" in done:
            argv += ["--documents", str(folder / DOCUMENTS_NAME)]
        if meeting.granicus_host:
            argv += [
                "--granicus",
                meeting.granicus_host,
                "--view-id",
                str(meeting.granicus_view_id),
            ]
        if meeting.cablecast_host:
            argv += ["--cablecast", meeting.cablecast_host]
        if settings.diarize:
            argv.append("--diarize")
        _call(steps.meeting, argv, "meeting run")
        done.append("recap")
    if "script" not in done:
        txt, js = _script(steps, settings, folder)
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
            argv = ["roundup", "--out", str(folder / "recap.json")]
            for m in sorted(meetings, key=lambda m: m["day"]):
                argv += ["--recap", str(Path(m["folder"]) / "recap.json")]
            _call(steps.script, argv, "script roundup")
            done.append("recap")
        if "script" not in done:
            txt, js = _script(steps, settings, folder)
            entry.update(script_txt=str(txt), script_json=str(js))
            done.append("script")
        if "title" not in done:
            # No title that passes is not a failure: the episode keeps its dated title.
            recap = str(folder / "recap.json")
            steps.article(["title", "--recap", recap, "--city", str(settings.city)])
            done.append("title")
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
    """Meeting id -> the run entry, for every finished meeting and roundup."""
    by_id: dict[str, dict[str, Any]] = {}
    for entry in [*state.data["meetings"].values(), *state.data["roundups"].values()]:
        recap_path = Path(entry.get("folder", "")) / "recap.json"
        if entry.get("status") != "done" or not recap_path.is_file():
            continue
        recap = json.loads(recap_path.read_text(encoding="utf-8"))
        transcript = recap_path.with_name("transcript.json")
        if transcript.is_file():
            meeting_id = str(json.loads(transcript.read_text(encoding="utf-8"))["video_id"])
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
        name = f"{slug(meeting_id)}.json"
        wanted.add(name)
        out = folder / name
        if out.is_file() and built_from.get(meeting_id) == meta.get("content_hash"):
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
    log += _drop_twice_published(folder, wanted)
    for old in folder.glob("*.json"):
        if old.name not in wanted:
            old.unlink()
            log.append((f"{old.stem}: story is no longer published; removed from the site", False))
    return log


def _drop_twice_published(folder: Path, wanted: set[str]) -> list[tuple[str, bool]]:
    """Keep one story per story id. Two published videos of one meeting (a city that uploads
    the same session twice) give the same id, and the site refuses a story id used twice, so
    the later file (by name) is left off the site and the editor is told to kill one copy on
    the desk."""
    log: list[tuple[str, bool]] = []
    kept: dict[str, str] = {}
    for name in sorted(wanted):
        path = folder / name
        try:
            story_id = json.loads(path.read_text(encoding="utf-8")).get("id")
        except (OSError, ValueError, AttributeError):
            continue  # a missing or broken file is reported by the site build
        if not isinstance(story_id, str):
            continue
        if story_id in kept:
            path.unlink()  # built again next run, and left off again until one copy is killed
            why = (
                f"{path.stem}: same meeting as {kept[story_id]} (story {story_id}); "
                "left off the site so it is not published twice. Kill one copy on the desk"
            )
            log.append((why, True))
            continue
        kept[story_id] = path.stem
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
    log += _drop_unpublished_episodes(site, state, items)
    for path, meta in items:
        meeting_id = str(meta.get("meeting_id"))
        if meta.get("kind") != "episode":
            continue
        if meeting_id in built and built_from.get(meeting_id) == meta.get("content_hash"):
            continue
        entry = runs.get(meeting_id)
        if entry is None:
            log.append((f"{meeting_id}: published, but no run of it is in this work folder", False))
            continue
        folder = Path(entry["folder"])
        try:
            script_txt, script_json, audio = _desk_script(folder, entry, path, meta)
            if not (audio / "episode.mp3").is_file():
                engine = ["--engine", site.audio_engine] if site.audio_engine else []
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
        except StepFailed as exc:
            log.append((f"{meeting_id}: {exc}", True))
            continue
        episode = json.loads(out.read_text(encoding="utf-8"))
        name = episode["id"]
        shutil.copyfile(out, site.episodes / f"{name}.json")
        for source, target in (
            ("episode.mp3", f"{name}.mp3"),
            ("chapters.json", f"{name}-chapters.json"),
            ("transcript.vtt", f"{name}.vtt"),
        ):
            shutil.copyfile(audio / source, site.media / target)
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
