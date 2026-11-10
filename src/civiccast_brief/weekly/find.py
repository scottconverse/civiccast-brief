"""Find the city's meetings for a weekly run, from the sources in ``sources.json``.

- A **PrimeGov** portal lists every meeting with its documents. A meeting whose video is on
  YouTube carries that link, and an HTML agenda becomes the run's ``agenda.txt``.
- A **YouTube** channel's public feed lists its newest videos. A title must read as a meeting
  and name one of the source's bodies.
- A **Granicus** archive lists its clips with their names and dates.

The same video found by two sources is one meeting; the portal's agenda goes with it. A meeting
that cannot be run (no video, or a body no source covers) is kept with the reason, so the run
reports it instead of passing over it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from civiccast_brief.record.records.portal import body_name
from civiccast_brief.record.video.youtube.classify import MeetingClassifier
from civiccast_brief.sources import Source

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class Agenda:
    """An agenda as the pipeline reads it: where it came from and its item lines."""

    source_url: str
    items: tuple[str, ...]

    def text(self, retrieved: date) -> str:
        lines = [f"Source: {self.source_url}", f"Retrieved {retrieved.isoformat()}", "AGENDA"]
        return "\n".join([*lines, *self.items]) + "\n"


@dataclass(frozen=True)
class Found:
    """One meeting. ``video`` is a YouTube id or a Granicus clip id; empty when there is none."""

    body: str
    day: date
    title: str
    source_id: str
    video: str = ""
    granicus_host: str = ""
    granicus_view_id: int | None = None
    agenda: Agenda | None = None
    #: Why this meeting cannot be run; empty when it can.
    reason: str = ""

    @property
    def key(self) -> str:
        if self.granicus_host:
            return f"granicus:{self.granicus_host}/{self.video}"
        if self.video:
            return f"youtube:{self.video}"
        return f"{self.source_id}:{self.day.isoformat()}:{self.title}"


@dataclass
class Listers:
    """How each kind of source is read. The command line passes the real readers."""

    #: (portal url, first day, last day) -> meetings with .title, .start, .video_url, .documents
    primegov: Callable[[str, date, date], Sequence[Any]] | None = None
    #: (meeting) -> Agenda, or None when the meeting has no HTML agenda
    primegov_agenda: Callable[[str, Any], Agenda | None] | None = None
    #: (channel id) -> listings with .video_id, .title, .published
    youtube: Callable[[str], Sequence[Any]] | None = None
    #: (host, view id) -> clips with .clip_id, .name, .date ("October 6, 2026")
    granicus: Callable[[str, int], Sequence[Any]] | None = None
    classifier: MeetingClassifier = field(default_factory=MeetingClassifier)


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def match_body(title: str, bodies: Iterable[str]) -> str:
    """The listed body a title names, or "" when it names none of them.

    The body the title names must be the listed body, or the listed body must appear in the
    title as whole words. When several do, the longest wins ("Planning and Zoning Commission"
    over "Commission").
    """
    named = _fold(body_name(title))
    folded = f" {_fold(title)} "
    hits = [b for b in bodies if _fold(b) == named or f" {_fold(b)} " in folded]
    return max(hits, key=len) if hits else ""


def youtube_id(url: str | None) -> str:
    """The video id of a YouTube watch link, or ""."""
    if not url:
        return ""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        candidate = parts.path.strip("/")
    elif host.endswith("youtube.com"):
        candidate = (parse_qs(parts.query).get("v") or [""])[0]
    else:
        return ""
    return candidate if _VIDEO_ID.match(candidate) else ""


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for parse in (
        lambda t: datetime.fromisoformat(t).date(),
        lambda t: datetime.strptime(t, "%B %d, %Y").date(),
        lambda t: datetime.strptime(t, "%Y%m%d").date(),
    ):
        try:
            return parse(text)
        except ValueError:
            continue
    return None


def _primegov(source: Source, first: date, last: date, listers: Listers) -> list[Found]:
    found: list[Found] = []
    for meeting in listers.primegov(source.url, first, last):  # type: ignore[misc]
        title = str(meeting.title)
        body = match_body(title, source.bodies)
        if not body:
            continue
        day = _day(meeting.start)
        if day is None:
            continue
        video = youtube_id(meeting.video_url)
        agenda = listers.primegov_agenda(source.url, meeting) if listers.primegov_agenda else None
        reason = "" if video else "the portal lists no video for this meeting"
        if "cancel" in title.lower():
            reason = "the meeting was cancelled"
        found.append(Found(body, day, title, source.id, video, agenda=agenda, reason=reason))
    return found


def _youtube(source: Source, first: date, last: date, listers: Listers) -> list[Found]:
    found: list[Found] = []
    for video in listers.youtube(source.channel_id):  # type: ignore[misc]
        day = _day(video.published)
        title = str(video.title)
        if day is None or not first <= day <= last or not listers.classifier.is_meeting(title):
            continue
        body = match_body(title, source.bodies)
        reason = "" if body else f"the title names no body this source covers ({body_name(title)})"
        found.append(Found(body, day, title, source.id, str(video.video_id), reason=reason))
    return found


def _granicus(source: Source, first: date, last: date, listers: Listers) -> list[Found]:
    found: list[Found] = []
    view_id = int(source.view_id)
    for clip in listers.granicus(source.host, view_id):  # type: ignore[misc]
        day = _day(clip.date)
        if day is None or not first <= day <= last:
            continue
        title = str(clip.name)
        body = match_body(title, source.bodies) or (
            source.bodies[0] if len(source.bodies) == 1 else ""
        )
        reason = "" if body else "the clip name names no body this source covers"
        found.append(
            Found(
                body,
                day,
                title,
                source.id,
                str(clip.clip_id),
                granicus_host=source.host,
                granicus_view_id=view_id,
                reason=reason,
            )
        )
    return found


READERS = {"primegov": _primegov, "youtube": _youtube, "granicus": _granicus}


def find_meetings(
    sources: Sequence[Source], first: date, last: date, listers: Listers
) -> tuple[list[Found], list[str]]:
    """Every meeting from ``first`` to ``last``, oldest first, and one line per source that
    could not be read."""
    by_key: dict[str, Found] = {}
    problems: list[str] = []
    for source in sources:
        if getattr(listers, source.kind) is None:
            problems.append(f"{source.id}: no reader for {source.kind} sources")
            continue
        try:
            meetings = READERS[source.kind](source, first, last, listers)
        except Exception as exc:  # noqa: BLE001 - one broken source must not stop the others
            problems.append(f"{source.id}: {exc}")
            continue
        for meeting in meetings:
            held = by_key.get(meeting.key)
            if held is None:
                by_key[meeting.key] = meeting
            elif held.agenda is None and meeting.agenda is not None:
                by_key[meeting.key] = replace(held, agenda=meeting.agenda)
            elif held.reason and not meeting.reason:
                by_key[meeting.key] = replace(meeting, agenda=meeting.agenda or held.agenda)
    return sorted(by_key.values(), key=lambda m: (m.day, m.body, m.key)), problems
