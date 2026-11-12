"""Find the city's meetings for a weekly run, from the sources in ``sources.json``.

- A **PrimeGov** portal lists every meeting with its documents. A meeting whose video is on
  YouTube carries that link, an HTML agenda becomes the run's ``agenda.txt``, and its packet
  and minutes become ``documents.json`` (fetched when the run gets to them).
- A **YouTube** channel's public feed lists its newest videos. A title must read as a meeting
  and name one of the source's bodies.
- A **Granicus** archive lists its clips with their names and dates.
- A **Laserfiche**, **CivicPlus**, **CivicClerk**, **eScribe**, **web page** or **Google Drive**
  source lists a body's agendas, packets and minutes by date, and no video
  (:mod:`civiccast_brief.weekly.listed`). They go with the video meeting of the same body on the
  same day; a day with documents and no video is kept with that reason.

The same video found by two sources is one meeting; the portal's agenda and documents go
with it. A meeting that cannot be run (no video, or a body no source covers) is kept with the
reason, so the run reports it instead of passing over it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from functools import partial
from typing import Any
from urllib.parse import parse_qs, urlsplit

from civiccast_brief.record.records.portal import body_name
from civiccast_brief.record.video.watch import title_day
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
    #: Fetches the meeting's documents when called: (documents.json data, report lines). None
    #: when the source lists no documents (a channel or an archive).
    documents: Callable[[], tuple[dict[str, Any], list[str]]] | None = field(
        default=None, compare=False, repr=False
    )
    #: Why this meeting cannot be run; empty when it can.
    reason: str = ""
    connector_key: str = ""
    connector_revision: str = ""
    connector_capture: str = ""
    connector_prepare: Callable | None = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> str:
        if self.connector_key:
            return f"connector:{self.connector_key}"
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
    #: (portal url, meeting, body) -> (documents.json data, report lines); fetched when called
    primegov_documents: Callable[[str, Any, str], tuple[dict[str, Any], list[str]]] | None = None
    #: (channel id) -> listings with .video_id, .title, .published
    youtube: Callable[[str], Sequence[Any]] | None = None
    #: (host, view id) -> clips with .clip_id, .name, .date ("October 6, 2026")
    granicus: Callable[[str, int], Sequence[Any]] | None = None
    #: (source, first day, last day) -> listed.Doc rows, for the kinds in listed.KINDS
    documents: Callable[[Source, date, date], Sequence[Any]] | None = None
    #: (one meeting's listed.Doc rows) -> (documents.json data, report lines); fetched when called
    read_documents: Callable[[Sequence[Any]], tuple[dict[str, Any], list[str]]] | None = None
    classifier: MeetingClassifier = field(default_factory=MeetingClassifier)
    connector: Callable | None = None


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
        documents = None
        if listers.primegov_documents is not None:
            fetch = listers.primegov_documents
            documents = partial(fetch, source.url, meeting, body)
        found.append(
            Found(
                body,
                day,
                title,
                source.id,
                video,
                agenda=agenda,
                documents=documents,
                reason=reason,
            )
        )
    return found


def _youtube(source: Source, first: date, last: date, listers: Listers) -> list[Found]:
    found: list[Found] = []
    for video in listers.youtube(source.channel_id):  # type: ignore[misc]
        title = str(video.title)
        posted = _day(video.published)
        # The meeting's day is the one its title names; the video goes up later, often the next
        # day in UTC and sometimes weeks after. A video posted in the days is listed even when
        # its meeting was before them, so a late video is not passed over.
        day = title_day(title) or posted
        in_days = any(d is not None and first <= d <= last for d in (day, posted))
        if day is None or not in_days or not listers.classifier.is_meeting(title):
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


NO_VIDEO = "only the meeting's documents are listed; no source lists its video"


def _listed(source: Source, first: date, last: date, listers: Listers) -> list[Found]:
    """One meeting per body and day the source has documents for."""
    days: dict[tuple[str, date], list[Any]] = {}
    for doc in listers.documents(source, first, last):  # type: ignore[misc]
        days.setdefault((doc.body, doc.day), []).append(doc)
    found: list[Found] = []
    for (body, day), docs in days.items():
        documents = partial(listers.read_documents, docs) if listers.read_documents else None
        title = next((d.title for d in docs if d.title), f"{body} {day.isoformat()}")
        found.append(Found(body, day, title, source.id, documents=documents, reason=NO_VIDEO))
    return found


def _chain(*fetches: Callable[[], tuple[dict[str, Any], list[str]]] | None) -> Any:
    """One documents fetch that runs each of ``fetches`` and keeps all their documents."""
    present = [f for f in fetches if f is not None]
    if len(present) < 2:
        return present[0] if present else None

    def fetch() -> tuple[dict[str, Any], list[str]]:
        documents: list[Any] = []
        lines: list[str] = []
        for one in present:
            data, more = one()
            documents += data.get("documents") or []
            lines += more
        return {"format": 1, "documents": documents}, lines

    return fetch


READERS = {"primegov": _primegov, "youtube": _youtube, "granicus": _granicus}
LISTED_KINDS = ("laserfiche", "civicplus", "civicclerk", "escribe", "webpage", "gdrive")


def find_meetings(
    sources: Sequence[Source], first: date, last: date, listers: Listers
) -> tuple[list[Found], list[str]]:
    """Every meeting from ``first`` to ``last``, oldest first, and one line per source that
    could not be read."""
    by_key: dict[str, Found] = {}
    problems: list[str] = []
    listed: list[Found] = []
    for source in sources:
        reader = (
            listers.connector
            if source.kind == "connector"
            else _listed
            if source.kind in LISTED_KINDS
            else READERS.get(source.kind)
        )
        lister = "documents" if source.kind in LISTED_KINDS else source.kind
        if reader is None or getattr(listers, lister, None) is None:
            problems.append(f"{source.id}: no reader for {source.kind} sources")
            continue
        try:
            if source.kind == "connector":
                meetings, errors = listers.connector(source, first, last)
                problems.extend(errors)
            else:
                meetings = reader(source, first, last, listers)
        except Exception as exc:  # noqa: BLE001 - one broken source must not stop the others
            problems.append(f"{source.id}: {exc}")
            continue
        if reader is _listed:
            listed += meetings
            continue
        for meeting in meetings:
            held = by_key.get(meeting.key)
            if held is None:
                by_key[meeting.key] = meeting
            elif held.agenda is None and meeting.agenda is not None:
                by_key[meeting.key] = replace(
                    held, agenda=meeting.agenda, documents=held.documents or meeting.documents
                )
            elif held.reason and not meeting.reason:
                by_key[meeting.key] = replace(
                    meeting,
                    agenda=meeting.agenda or held.agenda,
                    documents=meeting.documents or held.documents,
                )
            elif held.documents is None and meeting.documents is not None:
                by_key[meeting.key] = replace(held, documents=meeting.documents)
    # Documents go with the body's video meeting that day; a day with none is its own entry.
    for meeting in listed:
        same = [
            key
            for key, held in by_key.items()
            if held.body == meeting.body and held.day == meeting.day and held.reason != NO_VIDEO
        ]
        if same:
            for key in same:
                held = by_key[key]
                by_key[key] = replace(held, documents=_chain(held.documents, meeting.documents))
            continue
        key = f"documents:{meeting.body}:{meeting.day.isoformat()}"
        held = by_key.get(key)
        if held is None:
            by_key[key] = meeting
        else:
            by_key[key] = replace(held, documents=_chain(held.documents, meeting.documents))
    return sorted(by_key.values(), key=lambda m: (m.day, m.body, m.key)), problems
