"""Meeting documents on the open web: which links are documents, and reading one's text.

A document is a PDF, a Google Doc or a Google Drive file. Reading one gives its first lines and the
first date written in it, which is what a person checks to see that the source is live.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild.fetch import Fetcher, FetchError
from civiccast_brief.citybuild.page import Link, find_dates, squash

if TYPE_CHECKING:
    from civiccast_brief.citybuild.browser import Browser

_GDOC: Final = re.compile(r"^/document/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,100})")
_DRIVE_FILE: Final = re.compile(r"^/file/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,100})")
_DOC_SUFFIXES: Final = (".pdf", ".docx", ".doc")
#: How many pages of a PDF are read for the proof lines.
PDF_PAGES: Final = 3


def is_document(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path
    if host == "docs.google.com":
        return bool(_GDOC.match(path))
    if host == "drive.google.com":
        return bool(_DRIVE_FILE.match(path))
    return path.lower().endswith(_DOC_SUFFIXES)


def download_url(url: str) -> str:
    """Where the document's text or bytes can be read without a sign-in."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "docs.google.com":
        found = _GDOC.match(parts.path)
        if found:
            return f"https://docs.google.com/document/d/{found[1]}/export?format=txt"
    if host == "drive.google.com":
        found = _DRIVE_FILE.match(parts.path)
        if found:
            return f"https://drive.google.com/uc?export=download&id={found[1]}"
    return url


def kind_of(text: str) -> str:
    """agenda, minutes, packet or document, from a title or a document's first lines."""
    lowered = text.casefold()
    for word in ("minutes", "agenda", "packet"):
        if word in lowered:
            return word
    return "document"


@dataclass(frozen=True)
class Read:
    """A document that was read: what it is and the lines that show it."""

    url: str
    what: str
    pages: int | None
    lines: list[str] = field(default_factory=list)
    first_date: date | None = None


def read_document(url: str, fetcher: Fetcher | Browser, title: str = "") -> Read:
    """Download one document and read its first lines. Raises FetchError when it cannot.

    ``fetcher`` is the plain client, or the browser for a site that opens only in one.
    """
    page = fetcher.get(download_url(url))
    content = page.content
    pages: int | None = None
    if content.startswith(b"%PDF"):
        text, pages = _pdf_text(content, url)
    elif "pdf" in page.content_type.lower() or urlsplit(url).path.lower().endswith(".pdf"):
        raise FetchError(f"{url} was to be a PDF but is not one")
    elif "html" in page.content_type.lower():
        raise FetchError(
            f"{url} answered with a web page, not the document (it may need a sign-in)"
        )
    else:
        text = page.content.decode("utf-8-sig", errors="replace")
    lines = [squash(line)[:160] for line in text.splitlines() if squash(line)]
    if not lines:
        raise FetchError(f"{url} has no text to read (a scan without text?)")
    head = " ".join(lines[:12])
    what = kind_of(head)
    if what == "document":
        what = kind_of(title)
    dates = find_dates(head)
    return Read(
        url=url, what=what, pages=pages, lines=lines[:4], first_date=dates[0] if dates else None
    )


def _pdf_text(content: bytes, url: str) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    # pypdf logs a warning for each broken index entry it repairs; city PDFs have hundreds.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(content))
        count = len(reader.pages)
        text = "\n".join(
            (reader.pages[n].extract_text() or "") for n in range(min(count, PDF_PAGES))
        )
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise FetchError(f"{url} is a PDF that could not be read: {exc}") from exc
    return text, count


def document_links(links: list[Link]) -> list[Link]:
    seen: set[str] = set()
    found: list[Link] = []
    for link in links:
        if is_document(link.url) and link.url not in seen:
            seen.add(link.url)
            found.append(link)
    return found
