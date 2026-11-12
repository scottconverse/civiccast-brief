"""The meeting's documents for the weekly run: its agenda packet and its minutes.

For one PrimeGov meeting :func:`read_documents` fetches

- the meeting's **packet** (its "Packet" document), and
- its **minutes**: the published "Minutes" document when the portal lists one, otherwise the
  draft minutes inside the packet of the body's next regular session, found the way the
  record's minutes reading finds them (:func:`~civiccast_brief.record.records.minutes.find_run`:
  a MINUTES heading over the body's name and this date, then pages whose draft numbers count on).

Each document's pages are read with pypdf, and ``documents.json`` keeps, for each one, its kind,
its portal URL, its SHA-256 and the text of every page it holds (by page number in that PDF, so
``url#page=N`` opens the page). A packet or minutes that is not there is a line for the report,
not a failure. Downloads go through the adapter with the run's own size limit, the one the other
document sources use (Boulder's 22 October 2026 study session packet is 340 MB, past the adapter's
200 MiB packet limit; the run reads the whole public record), and a pause comes between requests.
"""

from __future__ import annotations

import io
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from civiccast_brief.record.adapters.base import AdapterError
from civiccast_brief.record.records import minutes, portal
from civiccast_brief.record.records.pages import own_footer, printed_footers
from civiccast_brief.record.repo.rows import RecordPage
from civiccast_brief.weekly.listed import WHOLE_BYTES

DOCUMENTS_NAME = "documents.json"
PACKET = "packet"
MINUTES = "minutes"
#: Compiled PDF documents (the adapter downloads only these).
COMPILED = 1
#: How far ahead the next regular session is looked for (the record's own window).
AHEAD_DAYS = portal.DEFAULT_WINDOW_AHEAD_DAYS
#: Seconds between two requests to the portal.
PAUSE_S = 1.0
#: A document that could not be fetched or read: a line for the report.
_UNREAD = (AdapterError, PyPdfError, ValueError, OSError)


@dataclass(frozen=True)
class Read:
    """One downloaded PDF: its portal URL, hash and page texts (page 1 first)."""

    url: str
    sha256: str
    pages: tuple[str, ...]


def pdf_pages(content: bytes) -> tuple[str, ...]:
    """The text of every page of a PDF, page 1 first."""
    reader = PdfReader(io.BytesIO(content))
    return tuple((page.extract_text() or "").strip() for page in reader.pages)


@dataclass
class Fetcher:
    """Downloads through one adapter, pausing between requests and reading each PDF once."""

    adapter: Any
    pause_s: float = PAUSE_S
    sleep: Callable[[float], None] = time.sleep
    #: The largest document downloaded.
    limit_bytes: int = WHOLE_BYTES
    _read: dict[int, Read] = field(default_factory=dict)
    _asked: bool = False

    def _wait(self) -> None:
        if self._asked:
            self.sleep(self.pause_s)
        self._asked = True

    def read(self, document: Any) -> Read:
        if document.template_id not in self._read:
            self._wait()
            content = self.adapter.get_document(document, limit_bytes=self.limit_bytes)
            self._read[document.template_id] = Read(
                content.source_url, content.sha256, pdf_pages(content.content)
            )
        return self._read[document.template_id]

    def list_meetings(self, first: date, last: date) -> list[Any]:
        self._wait()
        return list(self.adapter.list_meetings(first, last))


def _compiled(meeting: Any, kind: str) -> Any | None:
    for document in meeting.documents:
        if document.compile_output_type == COMPILED and (
            portal.record_kind(document.template_name) == kind
        ):
            return document
    return None


def _entry(kind: str, read: Read, numbers: range | list[int], **extra: Any) -> dict[str, Any]:
    pages = [{"page": n, "text": read.pages[n - 1]} for n in numbers]
    return {"kind": kind, "url": read.url, "sha256": read.sha256, **extra, "pages": pages}


def _day(meeting: Any) -> date:
    start = meeting.start
    return start.date() if isinstance(start, datetime) else start


def _month_day(day: date) -> str:
    return f"{day:%B} {day.day}"


def _next_regular(fetcher: Fetcher, meeting: Any, body: str) -> Any | None:
    """The body's next regular session that is not cancelled, as minutes.py takes it."""
    held = _day(meeting)
    for other in fetcher.list_meetings(held + timedelta(days=1), held + timedelta(days=AHEAD_DAYS)):
        if other.id == meeting.id or other.start <= meeting.start:
            continue
        if portal.body_name(other.title).casefold() != body.casefold():
            continue
        if portal.meeting_type(other.title) != "regular":
            continue
        if portal.cancellation_signal(other.title, other.documents):
            continue
        return other
    return None


def _draft_minutes(fetcher: Fetcher, meeting: Any, body: str) -> tuple[dict[str, Any] | None, str]:
    later = _next_regular(fetcher, meeting, body)
    if later is None:
        return None, minutes.NO_LATER_SESSION
    when = _month_day(_day(later))
    packet = _compiled(later, PACKET)
    if packet is None:
        return None, minutes.EXPECTED_IN.format(month_day=when)
    read = fetcher.read(packet)
    rows = [
        RecordPage(
            id=n,
            record_id=packet.template_id,
            page_number=n,
            footer_page_number=own_footer(printed_footers(text), n),
            text=text,
            ocr_reason=None,
        )
        for n, text in enumerate(read.pages, start=1)
    ]
    run = minutes.find_run(rows, body_name=body, on=_day(meeting))
    if run is None:
        return None, minutes.NO_RUN_FOUND.format(month_day=when)
    entry = _entry(
        MINUTES, read, range(run.start_page, run.end_page + 1), draft=True, packet_of=when
    )
    return entry, f"draft minutes on pages {run.start_page}-{run.end_page} of the {when} packet"


def read_documents(fetcher: Fetcher, meeting: Any, body: str) -> tuple[dict[str, Any], list[str]]:
    """``documents.json`` for one portal meeting, and a report line per document."""
    documents: list[dict[str, Any]] = []
    lines: list[str] = []

    packet = _compiled(meeting, PACKET)
    if packet is None:
        lines.append("packet: the portal lists no packet for this meeting")
    else:
        try:
            read = fetcher.read(packet)
            documents.append(_entry(PACKET, read, range(1, len(read.pages) + 1)))
            lines.append(f"packet: {len(read.pages)} pages")
        except _UNREAD as exc:
            lines.append(f"packet: not read: {exc}")

    own = _compiled(meeting, MINUTES)
    try:
        if own is not None:
            read = fetcher.read(own)
            documents.append(_entry(MINUTES, read, range(1, len(read.pages) + 1), draft=False))
            lines.append(f"minutes: {len(read.pages)} pages")
        else:
            entry, line = _draft_minutes(fetcher, meeting, body)
            if entry is not None:
                documents.append(entry)
            lines.append(f"minutes: {line}")
    except _UNREAD as exc:
        lines.append(f"minutes: not read: {exc}")
    return {"format": 1, "documents": documents}, lines


def write_documents(folder: Path, data: dict[str, Any]) -> Path:
    path = folder / DOCUMENTS_NAME
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
