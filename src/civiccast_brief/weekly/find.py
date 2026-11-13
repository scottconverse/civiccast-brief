"""Find the city's meetings for a weekly run, from the sources in ``sources.json``.

- A **PrimeGov** portal lists every meeting with its documents. A meeting whose video is on
  YouTube carries that link, an HTML agenda becomes the run's ``agenda.txt``, and its packet
  and minutes become ``documents.json`` (fetched when the run gets to them).
- A **YouTube** channel's public feed lists its newest videos. A title must read as a meeting
  and name one of the source's bodies, or another body the city's sources list: a city's channel
  carries all its bodies' meetings, and setup names only the bodies its newest titles showed.
- A **Granicus** archive lists its clips with their names and dates.
- A **Cablecast** site lists its shows that have a video; a title must name one of the source's
  bodies (the site also carries shows that are not meetings).
- A **Laserfiche**, **CivicPlus**, **CivicClerk**, **eScribe**, **CivicWeb**, **web page** or **Google Drive**
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

from civiccast_brief.citybuild.read import shortened_match
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
    cablecast_host: str = ""
    agenda: Agenda | None = None
    #: Fetches the meeting's documents when called: (documents.json data, report lines). None
    #: when the source lists no documents (a channel or an archive).
    documents: Callable[[], tuple[dict[str, Any], list[str]]] | None = field(
        default=None, compare=False, repr=False
    )
    #: Why this meeting cannot be run; empty when it can.
    reason: str = ""

    @property
    def key(self) -> str:
        if self.granicus_host:
            return f"granicus:{self.granicus_host}/{self.video}"
        if self.cablecast_host:
            return f"cablecast:{self.cablecast_host}/{self.video}"
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
    #: (host) -> shows with .show_id, .name, .date ("October 6, 2026"); only shows with a video
    cablecast: Callable[[str], Sequence[Any]] | None = None
    #: (source, first day, last day) -> listed.Doc rows, for the kinds in listed.KINDS
    documents: Callable[[Source, date, date], Sequence[Any]] | None = None
    #: (one meeting's listed.Doc rows) -> (documents.json data, report lines); fetched when called
    read_documents: Callable[[Sequence[Any]], tuple[dict[str, Any], list[str]]] | None = None
    classifier: MeetingClassifier = field(default_factory=MeetingClassifier)
    #: The city's name (city.json): a title may leave it off a body's name.
    city: str = ""


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def match_body(title: str, bodies: Iterable[str], city: str = "") -> str:
    """The listed body a title names, or "" when it names none of them.

    The body the title names must be the listed body, or the listed body must appear in the
    title as whole words. When several do, the longest wins ("Planning and Zoning Commission"
    over "Commission"). A title may also leave off the city's name and a trailing "Meeting",
    by the same rule as ``city check`` (``citybuild.read.shortened_match``).
    """
    hits: list[str] = []
    # Also without a short name in parentheses: "Longmont Housing Authority(LHA) Advisory Board".
    for text in dict.fromkeys((title, re.sub(r"\s*\([^)]*\)\s*", " ", title))):
        named = _fold(body_name(text))
        folded = f" {_fold(text)} "
        lowered = " ".join(text.casefold().split())
        hits += [
            b
            for b in bodies
            if _fold(b) == named or f" {_fold(b)} " in folded or shortened_match(b, lowered, city)
        ]
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
        body = match_body(title, source.bodies, listers.city)
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


def _day_reason(title: str, last: date) -> str:
    """Why a channel video of a known body cannot run yet, or ''."""
    named = title_day(title)
    if named is None:
        # The meeting run takes the date from the title, so it would fail ("November 2026").
        return "the title names no meeting day"
    if named > last:
        # A live stream is listed days before it starts; it has no captions yet.
        return f"the meeting is on {named.isoformat()}, after this run"
    return ""


def _youtube(source: Source, first: date, last: date, listers: Listers) -> list[Found]:
    found: list[Found] = []
    for video in listers.youtube(source.channel_id):  # type: ignore[misc]
        title = str(video.title)
        posted = _day(video.published)
        # The meeting's day is the one its title names; the video goes up later, often the next
        # day in UTC and sometimes weeks after. A video posted in the days is listed even when
        # its meeting was before them, so a late video is not passed over.
        named = title_day(title)
        day = named or posted
        in_days = any(d is not None and first <= d <= last for d in (day, posted))
        if day is None or not in_days or not listers.classifier.is_meeting(title):
            continue
        body = match_body(title, source.bodies, listers.city)
        if body:
            reason = _day_reason(title, last)
        else:
            reason = f"the title names no body this source covers ({body_name(title)})"
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
        body = match_body(title, source.bodies, listers.city) or (
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


def _cablecast(source: Source, first: date, last: date, listers: Listers) -> list[Found]:
    found: list[Found] = []
    for show in listers.cablecast(source.host):  # type: ignore[misc]
        day = _day(show.date)
        if day is None or not first <= day <= last:
            continue
        title = str(show.name)
        # A Cablecast site carries every body's meetings and its shows too, so a title must
        # name one of the source's bodies; a show that names none is not a meeting of theirs.
        body = match_body(title, source.bodies, listers.city)
        if not body:
            continue
        found.append(
            Found(
                body,
                day,
                title,
                source.id,
                str(show.show_id),
                cablecast_host=source.host,
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


READERS = {
    "primegov": _primegov,
    "youtube": _youtube,
    "granicus": _granicus,
    "cablecast": _cablecast,
}
LISTED_KINDS = ("laserfiche", "civicplus", "civicclerk", "escribe", "civicweb", "webpage", "gdrive")


def _city_body(meeting: Found, city_bodies: Sequence[str], last: date, city: str = "") -> Found:
    """A channel video whose title names a body another of the city's sources lists."""
    if meeting.body:
        return meeting
    body = match_body(meeting.title, city_bodies, city)
    if not body:
        return meeting
    return replace(meeting, body=body, reason=_day_reason(meeting.title, last))


_DATE_WORD = re.compile(
    r"\d+|jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?"
    r"|sept?(ember)?|oct(ober)?|nov(ember)?|dec(ember)?"
)


def meeting_name(title: str, city: str = "") -> str:
    """A title without its date, the city, a short name in parentheses or "Meeting":
    "Historic Preservation Commission Nov. 5, 2026 Meeting" -> "historic preservation commission".
    A session's kind stays ("city council regular session", "city council study session")."""
    text = f" {_fold(re.sub(r'\([^)]*\)', ' ', title))} "
    if city:
        text = text.replace(f" {_fold(city)} ", " ")
    text = re.sub(r" part (\d+)(?= )", r" part\1", text)  # "Part 2" is another video
    words = [w for w in text.split() if w != "meeting" and not _DATE_WORD.fullmatch(w)]
    return " ".join(words)


def _one_video_per_meeting(by_key: dict[str, Found], city: str) -> None:
    """Two videos of one meeting (the portal's link and the channel's upload, or two uploads)
    would make two episodes of it. The first one found runs: a portal meeting, with its
    agenda, before a channel video."""
    kept: dict[tuple[str, date, str], Found] = {}
    for key, meeting in list(by_key.items()):
        if meeting.reason or not meeting.body or not meeting.video:
            continue
        same = (meeting.body, meeting.day, meeting_name(meeting.title, city))
        first = kept.setdefault(same, meeting)
        if first is not meeting:
            reason = f"another video of this meeting runs ({first.video})"
            by_key[key] = replace(meeting, reason=reason)


def find_meetings(
    sources: Sequence[Source], first: date, last: date, listers: Listers
) -> tuple[list[Found], list[str]]:
    """Every meeting from ``first`` to ``last``, oldest first, and one line per source that
    could not be read."""
    by_key: dict[str, Found] = {}
    problems: list[str] = []
    listed: list[Found] = []
    city_bodies = sorted({b for s in sources for b in s.bodies})
    for source in sources:
        reader = _listed if source.kind in LISTED_KINDS else READERS.get(source.kind)
        lister = "documents" if source.kind in LISTED_KINDS else source.kind
        if reader is None or getattr(listers, lister, None) is None:
            problems.append(f"{source.id}: no reader for {source.kind} sources")
            continue
        try:
            meetings = reader(source, first, last, listers)
        except Exception as exc:  # noqa: BLE001 - one broken source must not stop the others
            problems.append(f"{source.id}: {exc}")
            continue
        if reader is _listed:
            listed += meetings
            continue
        if source.kind == "youtube":
            meetings = [_city_body(m, city_bodies, last, listers.city) for m in meetings]
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
    _one_video_per_meeting(by_key, listers.city)
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
