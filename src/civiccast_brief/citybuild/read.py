"""Check a source by reading one real recent meeting of one body from it.

Each kind has one reader. Each returns a :class:`Proof`: the meeting it found, the document or the
captions it read, and the lines that show it, or the reason it found none.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild import browser, drive, laserfiche, onedrive, platforms
from civiccast_brief.citybuild.documents import (
    Read,
    document_links,
    kind_of,
    read_document,
)
from civiccast_brief.citybuild.fetch import Fetcher, FetchError
from civiccast_brief.citybuild.page import first_date, link_date, parse, squash
from civiccast_brief.record import credentials
from civiccast_brief.record.video.youtube.data_api import DataApiClient
from civiccast_brief.record.video.youtube.listing import VideoError
from civiccast_brief.sources import Source

COMMITTEES_PATH: Final = "/api/committee/GetCommitteeesListByShowInPublicPortal"
FEED_URL: Final = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
#: How many meetings or documents a reader tries before it says it found none it could read.
TRIES: Final = 5
#: A meeting page that follows a list is read only when its date is this close to today.
FOLLOW_TRIES: Final = 4
_MEETING_WORD: Final = re.compile(r"\b(meeting|session|hearing)\b", re.IGNORECASE)


@dataclass
class Proof:
    body: str
    kind: str
    address: str
    ok: bool = False
    meeting_date: date | None = None
    title: str = ""
    document: str = ""
    what: str = ""
    lines: list[str] = field(default_factory=list)
    problem: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "kind": self.kind,
            "address": self.address,
            "ok": self.ok,
            "meeting_date": self.meeting_date.isoformat() if self.meeting_date else None,
            "title": self.title,
            "document": self.document,
            "what": self.what,
            "lines": self.lines,
            "problem": self.problem,
        }

    def report(self) -> list[str]:
        head = f"{self.body} ({self.kind}, {self.address})"
        if not self.ok:
            return [f"NOT READ  {head}", f"  why: {self.problem}"]
        when = self.meeting_date.isoformat() if self.meeting_date else "date not printed"
        out = [
            f"READ      {head}",
            f"  meeting: {when}  {self.title}",
            f"  {self.what}: {self.document}",
        ]
        out += [f"  | {line}" for line in self.lines]
        return out


def names_for(body: str) -> list[str]:
    """The ways a body's name is written: as given, and without a part in parentheses."""
    name = squash(body).casefold()
    short = squash(re.sub(r"\([^)]*\)", " ", name))
    return [n for n in dict.fromkeys((name, short)) if n]


def body_matches(body: str, text: str) -> bool:
    lowered = squash(text).casefold()
    return any(name in lowered for name in names_for(body))


def read_latest(
    source: Source,
    body: str,
    fetcher: Fetcher,
    *,
    today: date | None = None,
    captions: bool = False,
    work_dir: Path | None = None,
) -> Proof:
    today = today or date.today()
    proof = Proof(body=body, kind=source.kind, address=source.address)
    readers: dict[str, Callable[..., None]] = {
        "primegov": _primegov,
        "youtube": _youtube,
        "granicus": _granicus,
        "webpage": _webpage,
        "gdrive": _gdrive,
        "civicclerk": _civicclerk,
        "escribe": _escribe,
        "civicplus": _civicplus,
        "laserfiche": _laserfiche,
    }
    try:
        if source.kind == "youtube":
            _youtube(proof, source, fetcher, today, captions, work_dir)
        else:
            readers[source.kind](proof, source, fetcher, today)
    except FetchError as exc:
        proof.ok = False
        proof.problem = str(exc)
    return proof


# ----- PrimeGov ---------------------------------------------------------------------------------


def committees(base_url: str, fetcher: Fetcher) -> list[tuple[int, str]]:
    rows = fetcher.get(base_url + COMMITTEES_PATH).json()
    found: list[tuple[int, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get("id"), int) and row.get("name"):
            found.append((row["id"], squash(str(row["name"]))))
    return found


def committee_for(body: str, rows: list[tuple[int, str]]) -> int | None:
    names = names_for(body)
    for committee_id, name in rows:
        if name.casefold() in names or names_for(name)[-1] in names:
            return committee_id
    for committee_id, name in rows:
        if any(n in name.casefold() for n in names):
            return committee_id
    return None


def _cancelled(meeting: dict[str, Any]) -> bool:
    if "cancel" in str(meeting.get("title") or "").casefold():
        return True
    docs = meeting.get("documentList") or []
    return bool(docs) and all(
        "cancel" in str(d.get("templateName") or "").casefold() for d in docs if isinstance(d, dict)
    )


def _document_rank(document: dict[str, Any]) -> int | None:
    """Agenda first (HTML before PDF), then packet, then minutes; None for anything else."""
    name = str(document.get("templateName") or "").casefold()
    html_type = document.get("compileOutputType") == 3
    if "cancel" in name:
        return None
    if "agenda" in name:
        return 0 if html_type else 1
    if "packet" in name and not html_type:
        return 2
    if "minutes" in name and not html_type:
        return 3
    return None


def _primegov(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    from civiccast_brief.record.adapters.primegov import PrimeGovAdapter

    base = source.url.rstrip("/")
    rows = committees(base, fetcher)
    committee = committee_for(proof.body, rows)
    if committee is None:
        proof.problem = f"no committee named {proof.body!r} in the portal's list of {len(rows)}"
        return
    assert fetcher.client is not None
    adapter = PrimeGovAdapter(base, fetcher.client)
    meetings: dict[int, dict[str, Any]] = {}
    lists = [
        adapter.archived_meetings_url(today.year),
        adapter.archived_meetings_url(today.year - 1),
    ]
    lists.append(adapter.upcoming_meetings_url())
    for url in lists:
        payload = fetcher.get(url).json()
        for row in payload if isinstance(payload, list) else []:
            if isinstance(row, dict) and row.get("committeeId") == committee and row.get("id"):
                meetings.setdefault(int(row["id"]), row)
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for row in meetings.values():
        try:
            when = datetime.fromisoformat(str(row.get("dateTime")))
        except ValueError:
            continue
        if when.date() <= today and not _cancelled(row):
            dated.append((when, row))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    if not dated:
        proof.problem = f"no meeting of committee {committee} on or before {today} in {today.year - 1}-{today.year}"
        return
    tried: list[str] = []
    for when, row in dated[:TRIES]:
        documents = sorted(
            (
                d
                for d in row.get("documentList") or []
                if isinstance(d, dict) and _document_rank(d) is not None
            ),
            key=lambda d: _document_rank(d) or 0,
        )
        if not documents:
            tried.append(f"{when.date()}: no agenda, packet or minutes posted")
            continue
        for document in documents:
            template = int(document.get("templateId") or 0)
            try:
                if document.get("compileOutputType") == 3:
                    url = adapter.html_agenda_url(template)
                    items = adapter.parse_html_agenda(fetcher.get(url).content)
                    if not items:
                        tried.append(f"{when.date()}: the HTML agenda has no items")
                        continue
                    proof.lines = [f"{len(items)} agenda items"] + [
                        f"{item.number} {item.title}"[:160] for item in items[:3]
                    ]
                    proof.what = "agenda"
                else:
                    url = adapter.portal_document_url(template)
                    read = read_document(url, fetcher, str(document.get("templateName") or ""))
                    proof.lines = _read_lines(read)
                    proof.what = kind_of(str(document.get("templateName") or "")) or read.what
            except FetchError as exc:
                tried.append(f"{when.date()}: {exc}")
                continue
            proof.ok = True
            proof.meeting_date = when.date()
            proof.title = squash(str(row.get("title") or ""))
            proof.document = url
            return
    proof.problem = "; ".join(tried) or "nothing to read"


def _read_lines(read: Read) -> list[str]:
    head = []
    if read.pages is not None:
        head.append(f"{read.pages} pages")
    if read.first_date:
        head.append(f"first date in it: {read.first_date.isoformat()}")
    return ([", ".join(head)] if head else []) + read.lines


# ----- YouTube ----------------------------------------------------------------------------------

_FEED_ENTRY: Final = re.compile(
    r"<entry>.*?<yt:videoId>([A-Za-z0-9_-]{11})</yt:videoId>.*?<title>(.*?)</title>.*?"
    r"<published>([0-9T:+.-]+Z?|[0-9T:+.-]+)</published>",
    re.DOTALL,
)


def feed_entries(channel_id: str, fetcher: Fetcher) -> list[tuple[str, str, date]]:
    """The channel's newest videos: id, title and day published.

    With a saved YouTube Data API key the API lists the newest 50; when it fails, or with no
    key, the public feed lists 15 (and some channels' feeds answer 404).
    """
    api_problem = ""
    key = credentials.youtube_api_key()
    client = getattr(fetcher, "client", None)
    if key and client is not None:
        try:
            return _api_entries(DataApiClient(key, client).list_channel_videos(channel_id))
        except VideoError as exc:
            api_problem = str(exc)
    try:
        page = fetcher.get(FEED_URL.format(channel_id=channel_id))
    except FetchError as exc:
        if api_problem:
            raise FetchError(f"{api_problem}; then {exc}") from exc
        raise
    found: list[tuple[str, str, date]] = []
    for match in _FEED_ENTRY.finditer(page.text):
        try:
            day = datetime.fromisoformat(match[3]).date()
        except ValueError:
            continue
        found.append((match[1], squash(html.unescape(match[2])), day))
    return found


def _api_entries(videos: list) -> list[tuple[str, str, date]]:
    found: list[tuple[str, str, date]] = []
    for video in videos:
        try:
            day = datetime.fromisoformat(str(video.published)).date()
        except ValueError:
            continue
        found.append((video.video_id, squash(video.title), day))
    return found


def _youtube(
    proof: Proof,
    source: Source,
    fetcher: Fetcher,
    today: date,
    captions: bool = False,
    work_dir: Path | None = None,
) -> None:
    videos = [v for v in feed_entries(source.channel_id, fetcher) if body_matches(proof.body, v[1])]
    # A livestream can be set up weeks before the meeting, so a date in the title wins over the
    # day the video went up.
    videos = [(v[0], v[1], first_date(v[1]) or v[2]) for v in videos]
    videos = [v for v in videos if v[2] <= today]
    if not videos:
        proof.problem = f"none of the channel's newest videos names {proof.body!r} in its title"
        return
    video_id, title, day = max(videos, key=lambda v: v[2])
    proof.title = title
    proof.meeting_date = day
    proof.document = f"https://www.youtube.com/watch?v={video_id}"
    if not captions:
        proof.what = "video (captions not fetched; check with --captions)"
        proof.lines = [f"published {day.isoformat()}"]
        proof.ok = True
        return
    from civiccast_brief.meeting.capture import CaptureError, capture_captions
    from civiccast_brief.record import pacing

    try:
        pacing.pacer().wait(what="the city check captions")
    except pacing.PaceHeld as exc:
        proof.problem = str(exc)
        return
    folder = (work_dir or Path.cwd() / "city-check") / video_id
    try:
        capture = capture_captions(video_id, folder)
    except CaptureError as exc:
        text = str(exc)
        if "429" in text or "too many requests" in text.casefold():
            pacing.pacer().rate_limited(what="the city check captions", marker="yt-dlp")
        proof.problem = text
        return
    if not capture.segments:
        proof.problem = f"{video_id} has a caption file with no lines"
        return
    proof.ok = True
    proof.what = "captions"
    proof.lines = [f"{len(capture.segments)} caption lines, file {capture.caption_file.name}"] + [
        squash(str(s["text"]))[:160] for s in capture.segments[:3]
    ]


# ----- Granicus ---------------------------------------------------------------------------------


def _granicus(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    from civiccast_brief.meeting import granicus

    try:
        clips = granicus.list_clips(source.host, int(source.view_id))
    except granicus.GranicusError as exc:
        raise FetchError(str(exc)) from exc
    dated = []
    for clip in clips:
        day = granicus.long_date(clip.date)
        if day and body_matches(proof.body, clip.name):
            # long_date writes "October 6, 2026", not an ISO date.
            when = datetime.strptime(day, "%B %d, %Y").date()
            if when <= today:
                dated.append((when, clip))
    if not dated:
        proof.problem = f"no clip in view {source.view_id} names {proof.body!r}"
        return
    when, clip = max(dated, key=lambda pair: pair[0])
    proof.ok = True
    proof.meeting_date = when
    proof.title = clip.name
    proof.what = "video clip (captions read by the meeting command)"
    proof.document = granicus.video_url(source.host, int(source.view_id), clip.clip_id)


# ----- Web page ---------------------------------------------------------------------------------


def _webpage(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    page = fetcher.get(source.url)
    _, links, _ = parse(page.text, page.url)
    if source.follow:
        _follow(proof, source, links, fetcher, today)
        return
    dated = [(link_date(link), link) for link in document_links(links)]
    usable = [(d, link) for d, link in dated if d is not None and d <= today]
    if not usable:
        proof.problem = f"{len(dated)} document links on the page, none dated on or before {today}"
        return
    usable.sort(key=lambda pair: (pair[0], kind_of(pair[1].text) == "agenda"), reverse=True)
    _read_first(proof, usable, fetcher, "")


def _follow(proof: Proof, source: Source, links: list, fetcher: Fetcher, today: date) -> None:
    meetings: dict[str, tuple[date, Any]] = {}
    names: dict[str, str] = {}  # every link text for a page; a list may link it more than once
    for link in links:
        path = link.url.split("://", 1)[-1].split("/", 1)[-1]
        if not ("/" + path).startswith(source.follow):
            continue
        names[link.url] = names.get(link.url, "") + " " + link.text
        day = link_date(link)
        if day and day <= today and link.url not in meetings:
            meetings[link.url] = (day, link)
    # A cancelled meeting is skipped, and a page named as a meeting comes before a notice
    # such as "Member Events for November".
    ordered = sorted(
        (pair for url, pair in meetings.items() if "cancel" not in names[url].casefold()),
        key=lambda pair: (_MEETING_WORD.search(names[pair[1].url]) is not None, pair[0]),
        reverse=True,
    )
    if not ordered:
        proof.problem = f"no dated link under {source.follow} on the page"
        return
    tried: list[str] = []
    for day, link in ordered[:FOLLOW_TRIES]:
        page = fetcher.try_get(link.url)
        if page is None:
            tried.append(f"{day}: {link.url} could not be read")
            continue
        _, inner, _ = parse(page.text, page.url)
        docs = document_links(inner)
        if not docs:
            tried.append(f"{day}: no documents on {link.url}")
            continue
        docs.sort(key=lambda d: kind_of(d.text + " " + d.file_name) != "agenda")
        _read_first(proof, [(day, d) for d in docs], fetcher, link.text)
        if proof.ok:
            return
        tried.append(f"{day}: {proof.problem}")
    proof.problem = "; ".join(tried)


def _read_first(proof: Proof, candidates: list, fetcher: Fetcher, title: str) -> None:
    errors: list[str] = []
    for day, link in candidates[:TRIES]:
        # A OneDrive share opens only in a browser.
        reader = onedrive.Reader(browser.shared()) if onedrive.is_share(link.url) else fetcher
        try:
            read = read_document(link.url, reader, link.text + " " + link.file_name)
        except FetchError as exc:
            errors.append(str(exc))
            continue
        proof.ok = True
        proof.meeting_date = day
        proof.title = title or link.text
        proof.document = link.url
        proof.what = read.what
        proof.lines = _read_lines(read)
        proof.problem = ""
        return
    proof.problem = "; ".join(errors) or "no document could be read"


# ----- Google Drive -----------------------------------------------------------------------------


def _gdrive(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    files, notes = drive.walk(source.folder_id, fetcher, today)
    readable = [f for f in files if f.modified and f.modified <= today + timedelta(days=1)]
    if not readable:
        proof.problem = "; ".join([f"{len(files)} files in the folder, none dated", *notes])
        return
    readable.sort(key=lambda f: ("agenda" in f.title.casefold(), f.modified), reverse=True)
    errors: list[str] = []
    for entry in readable[:TRIES]:
        try:
            read = read_document(entry.url, fetcher, entry.title)
        except FetchError as exc:
            errors.append(str(exc))
            continue
        proof.ok = True
        proof.meeting_date = read.first_date or entry.modified
        proof.title = entry.title
        proof.document = entry.url
        proof.what = read.what
        proof.lines = [f"Drive says modified {entry.modified}"] + _read_lines(read)
        return
    proof.problem = "; ".join(errors + notes)


# ----- CivicClerk, eScribe and CivicPlus --------------------------------------------------------

_NOTICE: Final = re.compile(r"\bnotice\b", re.IGNORECASE)


def _read_docs(proof: Proof, docs: list[platforms.Doc], fetcher: Fetcher | browser.Browser) -> None:
    """Read the first of ``docs`` that opens; they come newest and most useful first."""
    errors: list[str] = []
    for doc in docs[:TRIES]:
        try:
            read = read_document(doc.url, fetcher, f"{doc.title} {doc.what}")
        except FetchError as exc:
            errors.append(f"{doc.day}: {exc}")
            continue
        proof.ok = True
        proof.meeting_date = doc.day
        proof.title = doc.title
        proof.document = doc.url
        proof.what = doc.what
        proof.lines = _read_lines(read)
        proof.problem = ""
        return
    proof.problem = "; ".join(errors) or "no document could be read"


def _civicclerk(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    docs = [
        d
        for d in platforms.clerk_docs(source.tenant, fetcher, today)
        if body_matches(proof.body, d.body)
    ]
    if not docs:
        proof.problem = (
            f"none of the portal's newest {platforms.CLERK_EVENTS} meetings on or before {today} "
            f"is a {proof.body!r} meeting with an agenda posted"
        )
        return
    _read_docs(proof, docs, fetcher)


def _escribe(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    meetings = [
        m
        for m in platforms.escribe_meetings(source.tenant, fetcher, today)
        if body_matches(proof.body, f"{m.get('MeetingName')} {m.get('MeetingType')}")
        and "cancel" not in str(m.get("MeetingName") or "").casefold()
    ]
    if not meetings:
        proof.problem = (
            f"no {proof.body!r} meeting in the calendar's last {platforms.ESCRIBE_DAYS} days"
        )
        return
    base = platforms.escribe_base(source.tenant)
    tried: list[str] = []
    for meeting in meetings[:TRIES]:
        day = meeting["day"]
        links = platforms.escribe_html_links(meeting)
        if not links:
            tried.append(f"{day}: no agenda or minutes page posted")
            continue
        for what, path in links:
            url = base + path if path.startswith("/") else path
            try:
                page = fetcher.get(url)
            except FetchError as exc:
                tried.append(f"{day}: {exc}")
                continue
            title, items = platforms.escribe_items(page.text)
            if not items:
                tried.append(f"{day}: the {what} page {url} has no numbered items")
                continue
            proof.ok = True
            proof.meeting_date = day
            proof.title = title or squash(str(meeting.get("MeetingName") or ""))
            proof.document = url
            proof.what = what
            proof.lines = [f"{len(items)} {what} items"] + [item[:160] for item in items[:3]]
            return
    proof.problem = "; ".join(tried)


def _civicplus(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    """The Agenda Center first; the Archive Center when the Agenda Center has nothing for the body.

    A notice ("Public Hearing Notice - BOT May Attend ...") is read only when no meeting's agenda
    or minutes can be.
    """
    looked: list[str] = []
    docs: list[platforms.Doc] = []
    for path, parse_page in (
        ("/AgendaCenter", platforms.agenda_center),
        ("/Archive.aspx", platforms.archive_center),
    ):
        page = fetcher.try_get(f"https://{source.site}{path}")
        if page is None:
            looked.append(f"{path} did not answer")
            continue
        host = (urlsplit(page.url).hostname or source.site).lower()
        listed = parse_page(host, page.text)
        docs = [d for d in listed if body_matches(proof.body, d.body) and d.day <= today]
        docs = [d for d in docs if "cancel" not in d.title.casefold()]
        if docs:
            break
        looked.append(f"{path} lists {len(listed)} dated documents, none for {proof.body!r}")
    if not docs:
        proof.problem = "; ".join(looked)
        return
    docs.sort(
        key=lambda d: (_NOTICE.search(d.title) is None, d.day, d.what == "agenda"), reverse=True
    )
    _read_docs(proof, docs, fetcher)


# ----- Laserfiche WebLink -----------------------------------------------------------------------


def _laserfiche(proof: Proof, source: Source, fetcher: Fetcher, today: date) -> None:
    """A WebLink folder opens only in a browser; it is read in the installed Chrome.

    The folder is the body's own, so every dated document in it is the body's.
    """
    reader = browser.shared()
    docs, notes = laserfiche.docs(reader, source.url, proof.body, today)
    if not docs:
        proof.problem = "; ".join(notes)
        return
    _read_docs(proof, docs, laserfiche.Reader(reader))
