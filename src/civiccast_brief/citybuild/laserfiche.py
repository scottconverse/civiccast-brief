"""Meeting documents in a Laserfiche WebLink folder, read in Chrome with rules and no model.

WebLink 11 (checked live on 2026-11-11 against Louisville, Colorado) shows a folder at
``/WebLink/Browse.aspx?id=<folder>&dbid=0&repo=<repo>``. The page asks
``/WebLink/FolderListingService.aspx/GetFolderListing2`` for the folder's entries: each has an
``entryId``, a ``type`` (0 a folder, -2 a document) and a ``name``. A document's file is
``/WebLink/ElectronicFile.aspx?docid=<entryId>&dbid=0&repo=<repo>`` when it is an electronic file
(``isEdoc``). A scanned document has no file there: the viewer
(``/WebLink/DocView.aspx?id=<entryId>``) shows its pages and asks
``DocumentService.aspx/GetTextHtmlForPage`` for each page's text, and that text is what is read.

A body's folder holds one folder per year ("2026 Planning Commission Agendas Packets Minutes")
and older folders for spans of years ("2000-2019 Planning Commission"). Its documents are named
with the meeting day ("Planning Commission Agenda and Packet 2026 10 08").

Some cities file a year by month and then by meeting (Boulder, checked live on 2026-11-11):
"2026" holds "01 JAN" ... "12 DEC", each month holds a folder per meeting ("10.06.26"), and that
holds the meeting's documents ("10.06.26 PB Agenda", "10.06.26 PB Audio").
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any, Final
from urllib.parse import parse_qs, quote, urlsplit

from civiccast_brief.citybuild.browser import Browser
from civiccast_brief.citybuild.documents import PDF_PAGES, kind_of
from civiccast_brief.citybuild.fetch import FetchError, Page
from civiccast_brief.citybuild.page import first_date, squash
from civiccast_brief.citybuild.platforms import Doc

LISTING: Final = "/FolderListingService.aspx/GetFolderListing2"
INFO: Final = "/DocumentService.aspx/GetBasicDocumentInfo"
TEXT: Final = "/DocumentService.aspx/GetTextHtmlForPage"
_ASK_HEADERS: Final = {"X-Lf-Suppress-Login-Redirect": "1"}
#: How many year folders are read, newest first: this year's and last year's.
YEARS: Final = 2
#: Entries asked for at once when the page's own list stops short of the folder.
PAGE: Final = 500
FOLDER: Final = 0
DOCUMENT: Final = -2
#: "2026 Planning Commission ..." is a year's folder; "2005-2019 ..." and "1999 & PRIOR ..." are
#: not.
_YEAR: Final = re.compile(r"^\s*(\d{4})\b(?!\s*(?:-|–|&|and\b|to\b))", re.IGNORECASE)
_TAG: Final = re.compile(r"<[^>]{0,2000}>")
#: How many months with meetings in them are read, newest first, where a year is filed by month.
MONTHS: Final = 3
#: Month folders listed at most, newest first, empty ones included.
MONTHS_LISTED: Final = 6
#: "10 OCT" or "10 October" is a month's folder.
_MONTH: Final = re.compile(r"^\s*(\d{1,2})\s+[a-z]{3}", re.IGNORECASE)
_NAME_DAY: Final = re.compile(r"(?<!\d)(\d{4})[ ._-](\d{1,2})[ ._-](\d{1,2})(?!\d)")


_RANK: Final = {"agenda": 3, "packet": 3, "minutes": 2}


class LaserficheError(ValueError):
    """An address is not a WebLink folder."""


def parse_url(url: str) -> tuple[str, str, int, str]:
    """``(host, base path, folder id, repo)`` of a WebLink folder's Browse.aspx address."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").lower()
    path = parts.path
    if not host or not path.casefold().endswith("/browse.aspx"):
        raise LaserficheError("the address is a WebLink folder's, ending in Browse.aspx?id=...")
    query = {k.casefold(): v for k, v in parse_qs(parts.query).items()}
    folder = (query.get("id") or [""])[0]
    repo = (query.get("repo") or [""])[0]
    if not folder.isdigit() or not repo:
        raise LaserficheError("the address names the folder (id=...) and the repository (repo=...)")
    return host, path[: -len("/browse.aspx")], int(folder), repo


def browse_url(host: str, base: str, folder: int, repo: str) -> str:
    return f"https://{host}{base}/Browse.aspx?id={folder}&dbid=0&repo={quote(repo)}"


def file_url(host: str, base: str, doc: int, repo: str) -> str:
    return f"https://{host}{base}/ElectronicFile.aspx?docid={doc}&dbid=0&repo={quote(repo)}"


def view_url(host: str, base: str, doc: int, repo: str) -> str:
    return f"https://{host}{base}/DocView.aspx?id={doc}&dbid=0&repo={quote(repo)}"


def _results(data: Any) -> tuple[list[dict[str, Any]], int]:
    block = data.get("data") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return [], 0
    rows = [r for r in block.get("results") or [] if isinstance(r, dict)]
    total = block.get("totalEntries")
    return rows, total if isinstance(total, int) else len(rows)


def entries(browser: Browser, host: str, base: str, folder: int, repo: str) -> list[dict[str, Any]]:
    """Every entry of one folder, by opening its page in the browser.

    The page lists the first 30 entries; a folder with more is asked again for all of them, from
    the page, the way the page asks.
    """
    rows, total = _results(browser.open_and_catch(browse_url(host, base, folder, repo), LISTING))
    if total > len(rows):
        ask = {
            "repoName": repo,
            "folderId": folder,
            "getNewListing": True,
            "start": 0,
            "end": min(total, PAGE),
            "sortColumn": "",
            "sortAscending": True,
        }
        rows, _ = _results(browser.post_json(f"{base}{LISTING}", ask, _ASK_HEADERS))
    return rows


def year_of(name: str) -> int | None:
    match = _YEAR.match(name)
    return int(match.group(1)) if match else None


def month_of(name: str) -> int | None:
    """The month of a month's folder ("10 OCT"), or None."""
    match = _MONTH.match(name)
    month = int(match.group(1)) if match else 0
    return month if 1 <= month <= 12 else None


def day_of(name: str) -> date | None:
    """The meeting day in a document's name: "2026 10 08", or a written date."""
    match = _NAME_DAY.search(name)
    if match:
        try:
            return date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return None
    return first_date(name)


def docs(browser: Browser, url: str, body: str, today: date) -> tuple[list[Doc], list[str]]:
    """The body folder's dated documents on or before ``today``, newest first, and notes.

    The body folder's own documents are read with those of its newest :data:`YEARS` year
    folders. On one day an agenda or packet comes before the minutes.
    """
    host, base, folder, repo = parse_url(url)
    top = entries(browser, host, base, folder, repo)
    found = [r for r in top if r.get("type") == DOCUMENT]
    years = sorted(
        (
            (year, r)
            for r in top
            if r.get("type") == FOLDER
            and (year := year_of(str(r.get("name") or ""))) is not None
            and year <= today.year
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    notes = [f"the folder has {len(top)} entries, {len(years)} of them year folders"]
    months: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for year, row in years[:YEARS]:
        for r in entries(browser, host, base, int(row["entryId"]), repo):
            if r.get("type") == DOCUMENT:
                found.append(r)
            elif (
                r.get("type") == FOLDER
                and (month := month_of(str(r.get("name") or "")))
                and (year, month) <= (today.year, today.month)
            ):
                months.append(((year, month), r))
    months.sort(key=lambda pair: pair[0], reverse=True)
    with_meetings = 0
    for _when, row in months[:MONTHS_LISTED]:
        if with_meetings >= MONTHS:
            break
        before = len(found)
        for meeting in entries(browser, host, base, int(row["entryId"]), repo):
            if meeting.get("type") == DOCUMENT:
                found.append(meeting)
            elif meeting.get("type") == FOLDER:
                day = day_of(squash(str(meeting.get("name") or "")))
                if day is not None and day <= today:
                    found += [
                        r
                        for r in entries(browser, host, base, int(meeting["entryId"]), repo)
                        if r.get("type") == DOCUMENT
                    ]
        with_meetings += len(found) > before
    if months:
        notes.append(f"{len(months)} month folders, the newest {with_meetings} with meetings read")
    listed: list[Doc] = []
    for row in found:
        name = squash(str(row.get("name") or ""))
        day = day_of(name)
        if day is None or day > today or "cancel" in name.casefold():
            continue
        what = kind_of(name)
        doc = int(row["entryId"])
        url_of = (file_url if row.get("isEdoc") else view_url)(host, base, doc, repo)
        listed.append(Doc(body, day, name, url_of, "agenda" if what == "document" else what))
    notes.append(f"{len(found)} documents read from it, {len(listed)} dated on or before {today}")
    # An agenda or packet first, then the minutes, then a document the name does not say is one
    # (a recording, a staff memo).
    listed.sort(key=lambda d: (d.day, _RANK.get(kind_of(d.title), 0)), reverse=True)
    return listed, notes


def scanned_text(browser: Browser, url: str) -> Page:
    """The text of a scanned document's first pages, asked from its viewer the way the viewer
    asks. Raises FetchError when the viewer has no text for them."""
    parts = urlsplit(url)
    query = {k.casefold(): v for k, v in parse_qs(parts.query).items()}
    doc = (query.get("id") or [""])[0]
    repo = (query.get("repo") or [""])[0]
    if not doc.isdigit() or not repo:
        raise FetchError(f"{url} is not a WebLink document viewer address")
    base = parts.path[: -len("/DocView.aspx")]
    info = browser.open_and_catch(url, INFO)
    block = info.get("data") if isinstance(info, dict) else None
    count = block.get("pageCount") if isinstance(block, dict) else None
    if not isinstance(count, int) or count < 1:
        raise FetchError(f"{url}: the viewer lists no pages")
    texts = []
    for number in range(1, min(count, PDF_PAGES) + 1):
        ask = {
            "repoName": repo,
            "documentId": int(doc),
            "pageNum": number,
            "showAnn": True,
            "searchUuid": "",
        }
        out = browser.post_json(f"{base}{TEXT}", ask, _ASK_HEADERS)
        page = out.get("data") if isinstance(out, dict) else None
        text = page.get("text") if isinstance(page, dict) else None
        if isinstance(text, str):
            texts.append(html.unescape(_TAG.sub("", text)))
    if not any(t.strip() for t in texts):
        raise FetchError(f"{url} has no text to read (a scan without text?)")
    content = "\n".join(texts).encode()
    return Page(url=url, status=200, content=content, content_type="text/plain")


class Reader:
    """The browser, reading a folder's documents: an electronic file is downloaded, a scanned
    one is read from its viewer's page text."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser

    def open_and_catch(self, url: str, part: str) -> Any:
        return self.browser.open_and_catch(url, part)

    def post_json(self, url: str, data: Any, headers: dict[str, str] | None = None) -> Any:
        return self.browser.post_json(url, data, headers)

    def get(self, url: str) -> Page:
        if urlsplit(url).path.casefold().endswith("/docview.aspx"):
            return scanned_text(self.browser, url)
        return self.browser.get(url)
