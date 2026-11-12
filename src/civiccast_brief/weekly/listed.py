"""Meetings from the sources that list documents but no video, for the weekly run.

A Laserfiche folder, a CivicPlus Agenda or Archive Center, a CivicClerk or eScribe portal, a
plain web page and a Google Drive folder each list a body's agendas, packets and minutes by date.
:func:`list_docs` gives every one dated in the run's days, for the bodies the source covers, with
the readers that ``city check`` uses. :func:`read_all` reads each document in full for
``documents.json``: every page of a PDF, a Word file or a text export as one page, and an eScribe
HTML agenda's numbered items as one page.

The weekly run joins these documents to the video meeting of the same body on the same day. A
day with documents and no video is listed with the reason, not passed over.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild import browser, drive, laserfiche, onedrive, platforms
from civiccast_brief.citybuild.documents import (
    _docx_text,
    document_links,
    download_url,
    kind_of,
    read_document,
)
from civiccast_brief.citybuild.fetch import Fetcher, FetchError
from civiccast_brief.citybuild.page import first_date, link_date, parse, squash
from civiccast_brief.citybuild.read import _MEETING_WORD, body_matches
from civiccast_brief.sources import Source

#: The kinds this module reads.
KINDS: Final = ("laserfiche", "civicplus", "civicclerk", "escribe", "webpage", "gdrive")
#: The most pages read from one scanned Laserfiche document.
SCANNED_PAGES: Final = 400
#: A Drive file with no day in its name is opened when it was changed this long before the
#: run's first day (an agenda posted ahead) or after its last (minutes posted later).
#: The largest document read in full. Louisville's 2 November 2026 council packet is over the
#: city check's DOCUMENT_BYTES (150 MB); the run reads the whole public record.
WHOLE_BYTES: Final = 1_000_000_000
DRIVE_EARLY: Final = timedelta(days=30)
DRIVE_LATE: Final = timedelta(days=45)


@dataclass(frozen=True)
class Doc:
    """One meeting document: the listed body it is for, its day, title, address and kind."""

    body: str
    day: date
    title: str
    url: str
    what: str
    #: "laserfiche" when the document opens only in the browser, as a WebLink one does.
    via: str = ""


def _listed_body(source: Source, named: str) -> str:
    """The source's listed body that ``named`` (the platform's name for it) is, or ""."""
    hits = [b for b in source.bodies if body_matches(b, named)]
    return max(hits, key=len) if hits else ""


def _keep(source: Source, docs: Sequence[Any], first: date, last: date, via: str = "") -> list[Doc]:
    kept: list[Doc] = []
    seen: set[str] = set()
    for d in docs:
        # A document the platform files under another body is not this source's. One with no
        # body named (a plain page, a Drive folder) is the source's when it lists one body.
        if d.body:
            body = _listed_body(source, d.body)
        else:
            body = source.bodies[0] if len(source.bodies) == 1 else ""
        if not body or not first <= d.day <= last or "cancel" in d.title.casefold():
            continue
        if d.url in seen:
            continue
        seen.add(d.url)
        kept.append(Doc(body, d.day, d.title, d.url, d.what, via))
    return kept


def _civicclerk(source: Source, first: date, last: date, fetcher: Fetcher) -> list[Doc]:
    return _keep(source, platforms.clerk_docs(source.tenant, fetcher, last), first, last)


def _escribe(source: Source, first: date, last: date, fetcher: Fetcher) -> list[Doc]:
    base = platforms.escribe_base(source.tenant)
    docs = []
    for meeting in platforms.escribe_meetings(source.tenant, fetcher, last):
        named = f"{meeting.get('MeetingName')} {meeting.get('MeetingType')}"
        body = _listed_body(source, platforms.escribe_body(named)) or _listed_body(source, named)
        if not body:
            continue  # another body's meeting on the county's calendar
        title = squash(str(meeting.get("MeetingName") or ""))
        for what, path in platforms.escribe_html_links(meeting):
            url = base + path if path.startswith("/") else path
            docs.append(platforms.Doc(body, meeting["day"], title, url, what))
    return _keep(source, docs, first, last)


def _civicplus(source: Source, first: date, last: date, fetcher: Fetcher) -> list[Doc]:
    docs: list[Any] = []
    for path, parse_page in (
        ("/AgendaCenter", platforms.agenda_center),
        ("/Archive.aspx", platforms.archive_center),
    ):
        page = fetcher.try_get(f"https://{source.site}{path}")
        if page is None:
            continue
        host = (urlsplit(page.url).hostname or source.site).lower()
        docs += parse_page(host, page.text)
    return _keep(source, docs, first, last)


def _laserfiche(source: Source, first: date, last: date, fetcher: Fetcher) -> list[Doc]:
    # The folder is one body's, so its documents are that body's.
    body = source.bodies[0] if source.bodies else ""
    try:
        found, _notes = laserfiche.docs(browser.shared(), source.url, body, last)
    except FetchError:
        # The first folder opened after Chrome starts can time out (seen with Louisville's,
        # 2026-11-11); a second try opens it.
        found, _notes = laserfiche.docs(browser.shared(), source.url, body, last)
    return _keep(source, found, first, last, via="laserfiche")


def _webpage(source: Source, first: date, last: date, fetcher: Fetcher) -> list[Doc]:
    page = fetcher.get(source.url)
    _, links, _ = parse(page.text, page.url)
    docs: list[Any] = []
    if source.follow:
        for link in links:
            path = "/" + link.url.split("://", 1)[-1].split("/", 1)[-1]
            day = link_date(link)
            if not path.startswith(source.follow) or day is None or not first <= day <= last:
                continue
            # "Member Events for November" is on the list too, and is not a meeting.
            if not _MEETING_WORD.search(link.text):
                continue
            inner = fetcher.try_get(link.url)
            if inner is None:
                continue
            _, inner_links, _ = parse(inner.text, inner.url)
            for doc in document_links(inner_links):
                what = kind_of(f"{doc.text} {doc.file_name}")
                docs.append(platforms.Doc("", day, link.text, doc.url, what))
    else:
        for link in document_links(links):
            day = link_date(link)
            if day is not None:
                what = kind_of(f"{link.text} {link.file_name}")
                docs.append(platforms.Doc("", day, link.text, link.url, what))
    return _keep(source, docs, first, last)


def _gdrive(source: Source, first: date, last: date, fetcher: Fetcher) -> list[Doc]:
    files, _notes = drive.walk(source.folder_id, fetcher, last + DRIVE_LATE)
    docs = []
    for entry in files:
        # The meeting day is the one in the file's name; the day it was changed is not it. A
        # name with no day ("November Agenda") is opened for the first date in it, when the file
        # was changed near the run's days.
        day = first_date(entry.title)
        if day is None:
            changed = entry.modified
            if changed is None or not first - DRIVE_EARLY <= changed <= last + DRIVE_LATE:
                continue
            try:
                day = read_document(entry.url, fetcher, entry.title).first_date
            except FetchError:
                continue
        if day is not None:
            docs.append(platforms.Doc("", day, entry.title, entry.url, kind_of(entry.title)))
    return _keep(source, docs, first, last)


READERS: Final[dict[str, Callable[[Source, date, date, Fetcher], list[Doc]]]] = {
    "laserfiche": _laserfiche,
    "civicplus": _civicplus,
    "civicclerk": _civicclerk,
    "escribe": _escribe,
    "webpage": _webpage,
    "gdrive": _gdrive,
}


def list_docs(source: Source, first: date, last: date, fetcher: Fetcher) -> list[Doc]:
    """Every document the source lists for its bodies from ``first`` to ``last``."""
    return READERS[source.kind](source, first, last, fetcher)


# ----- Reading a document in full ---------------------------------------------------------------


def _pdf_pages(content: bytes, url: str) -> tuple[list[str], list[int]]:
    """Each page's text, and the numbers of the pages whose text could not be taken out."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(content))
        listed = list(reader.pages)
    except (PyPdfError, ValueError, KeyError, TypeError) as exc:
        raise FetchError(f"{url} is a PDF that could not be read: {exc}") from exc
    pages: list[str] = []
    unread: list[int] = []
    for number, page in enumerate(listed, start=1):
        # One page can be past what pypdf will parse (a drawing with millions of marks, as in
        # Louisville's 2 November 2026 council packet); the other pages are still read.
        try:
            pages.append(page.extract_text() or "")
        except (PyPdfError, ValueError, KeyError, TypeError, RecursionError):
            pages.append("")
            unread.append(number)
    return pages, unread


def _get(doc: Doc, fetcher: Fetcher) -> Any:
    if doc.via == "laserfiche":
        return laserfiche.Reader(browser.shared(), pages=SCANNED_PAGES).get(doc.url)
    if onedrive.is_share(doc.url):
        return onedrive.Reader(browser.shared()).get(doc.url)
    return fetcher.get(download_url(doc.url), max_bytes=WHOLE_BYTES)


def read_pages(doc: Doc, fetcher: Fetcher) -> tuple[str, list[str], list[int]]:
    """The document's SHA-256, the text of each of its pages, and the numbers of any pages whose
    text could not be taken out. Raises FetchError."""
    page = _get(doc, fetcher)
    content = page.content
    unread: list[int] = []
    if content.startswith(b"%PDF"):
        pages, unread = _pdf_pages(content, doc.url)
    elif content.startswith(b"PK"):
        pages = [_docx_text(content, doc.url)]
    elif "html" in page.content_type.lower():
        title, items = platforms.escribe_items(page.text)
        if not items:
            raise FetchError(f"{doc.url} answered with a web page, not the document")
        pages = ["\n".join([title, *items]) if title else "\n".join(items)]
    else:
        # A scanned WebLink document comes as its pages' text with a form feed between pages.
        pages = content.decode("utf-8-sig", errors="replace").split("\f")
    if not any(p.strip() for p in pages):
        raise FetchError(f"{doc.url} has no text to read (a scan without text?)")
    return hashlib.sha256(content).hexdigest(), pages, unread


def read_all(docs: Sequence[Doc], fetcher: Fetcher) -> tuple[dict[str, Any], list[str]]:
    """``documents.json`` for one meeting's documents, and a report line for each."""
    documents: list[dict[str, Any]] = []
    lines: list[str] = []
    for doc in docs:
        try:
            sha, pages, unread = read_pages(doc, fetcher)
        except FetchError as exc:
            lines.append(f"{doc.what}: not read: {exc}")
            continue
        numbered = [{"page": n, "text": text} for n, text in enumerate(pages, start=1)]
        documents.append({"kind": doc.what, "url": doc.url, "sha256": sha, "pages": numbered})
        line = f"{doc.what}: {len(pages)} pages ({doc.url})"
        if unread:
            shown = ", ".join(str(n) for n in unread[:10]) + (" ..." if len(unread) > 10 else "")
            line += f"; no text taken from page {shown}"
        lines.append(line)
    return {"format": 1, "documents": documents}, lines
