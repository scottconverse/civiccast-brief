"""The weekly run: every new meeting becomes a recap, a script, an episode and a story.

For each meeting :mod:`find` turned up, in date order:

1. ``agenda.txt`` from the portal's HTML agenda, when there is one;
2. ``meeting run`` (captions, speakers, recap) with the body's roster;
3. ``script write``; when the check fails, once more with the report, then ``script repair``;
4. the episode goes on the editor desk (auto-publish per ``desk.json``);
5. ``article write`` and ``article stage``.

With two or more meetings done in the window, a roundup follows: ``script roundup``, a script,
a written title, and its episode on the desk.

Then, when the site settings are given: ``review export``; for each published episode not built
yet, ``audio``, ``episode assemble`` (with the published story of the same meeting), the media
files copied under their episode names; and ``site build``.

``weekly-state.json`` in the work folder records each meeting's steps, so a rerun picks up where
the last one stopped: a finished meeting is not run again, a failed one is tried again.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

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


def _stage_episode(steps: Steps, settings: Settings, folder: Path, script_json: Path) -> None:
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
            "--city",
            str(settings.city),
        ],
        "episode stage",
    )


def _article(steps: Steps, settings: Settings, folder: Path) -> str:
    """Write and stage the story. A story that fails its check still goes on the desk for
    review (it is never auto-published); no story at all is a failure."""
    recap = str(folder / "recap.json")
    out = folder / "article"
    city = ["--city", str(settings.city)]
    article = out.with_suffix(".json")
    article.unlink(missing_ok=True)
    steps.article(["write", "--recap", recap, "--out", str(out), *city])
    if not article.exists():
        raise StepFailed("article write failed (no story was written)")
    _call(
        steps.article,
        ["stage", "--db", str(settings.db), "--recap", recap, "--article", str(article), *city],
        "article stage",
    )
    return str(article)


def run_meeting(steps: Steps, settings: Settings, meeting: Found, entry: dict[str, Any]) -> None:
    """Run one meeting's steps, skipping the ones ``entry`` says are done. Updates ``entry``."""
    folder = settings.work / folder_for(meeting)
    folder.mkdir(parents=True, exist_ok=True)
    entry.update(folder=str(folder), body=meeting.body, day=meeting.day.isoformat())
    done: list[str] = entry.setdefault("done", [])
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
        if meeting.granicus_host:
            argv += [
                "--granicus",
                meeting.granicus_host,
                "--view-id",
                str(meeting.granicus_view_id),
            ]
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
    """The roundup of every meeting done in the window. Returns a line for the log."""
    meetings = [
        m
        for m in state.data["meetings"].values()
        if m.get("status") == "done" and m.get("week") == last.isoformat()
    ]
    if len(meetings) < 2:
        return f"roundup: not made ({len(meetings)} meeting(s) done this week; it needs 2)"
    key = last.isoformat()
    entry = state.data["roundups"].setdefault(key, {})
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
    site.episodes.mkdir(parents=True, exist_ok=True)
    site.media.mkdir(parents=True, exist_ok=True)
    for path, meta in items:
        meeting_id = str(meta.get("meeting_id"))
        if meta.get("kind") != "episode" or meeting_id in built:
            continue
        entry = runs.get(meeting_id)
        if entry is None:
            log.append((f"{meeting_id}: published, but no run of it is in this work folder", False))
            continue
        folder = Path(entry["folder"])
        audio = folder / "audio"
        try:
            if not (audio / "episode.mp3").is_file():
                engine = ["--engine", site.audio_engine] if site.audio_engine else []
                _call(steps.audio, [entry["script_txt"], "--out", str(audio), *engine], "audio")
            out = folder / "episode.json"
            argv = [
                "assemble",
                "--item", str(path),
                "--meeting", str(folder),
                "--script", entry["script_json"],
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
        state.save()
        log.append((f"{meeting_id}: built episode {name}", False))
    argv = ["build", "--episodes", str(site.episodes), "--out", str(site.out)]
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
        if meeting.reason:
            entry.update(status="not run", error=meeting.reason)
            state.save()
            report.add(f"{meeting.day} {meeting.title}: not run: {meeting.reason}")
            continue
        if entry.get("status") == "done":
            report.add(f"{meeting.day} {meeting.title}: done in an earlier run")
            continue
        try:
            run_meeting(steps, settings, meeting, entry)
        except StepFailed as exc:
            _record(state, entry, str(exc))
            report.add(f"{meeting.day} {meeting.title}: failed: {exc}", failed=True)
            continue
        _record(state, entry, "")
        report.add(f"{meeting.day} {meeting.title}: done")
    line = run_roundup(steps, settings, state, last)
    report.add(line, failed=line.startswith("roundup: failed"))
    if settings.site is not None:
        when = published or datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            for line, failed in publish(steps, settings, state, when):
                report.add(line, failed=failed)
        except StepFailed as exc:
            report.add(f"site: {exc}", failed=True)
    return report
