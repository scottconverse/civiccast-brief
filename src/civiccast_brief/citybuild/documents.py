"""Meeting documents on the open web: which links are documents, and reading one's text.

A document is a PDF, a Word file, a Google Doc, a Google Drive file or a OneDrive share link. Reading one gives its first lines and the
first date written in it, which is what a person checks to see that the source is live.
"""

from __future__ import annotations

import html
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild import onedrive
from civiccast_brief.citybuild.fetch import DOCUMENT_BYTES, Fetcher, FetchError
from civiccast_brief.citybuild.page import Link, find_dates, squash

if TYPE_CHECKING:
    from civiccast_brief.citybuild.browser import Browser

_GDOC: Final = re.compile(r"^/document/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,100})")
_DRIVE_FILE: Final = re.compile(r"^/file/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,100})")
_DOC_SUFFIXES: Final = (".pdf", ".docx", ".doc")
#: How many pages of a PDF are read for the proof lines.
PDF_PAGES: Final = 3
#: The most text a Word file's body may unpack to; a larger one is not read.
DOCX_BYTES: Final = 50_000_000
_PARAGRAPH: Final = re.compile(r"<w:p[ >].*?</w:p>", re.DOTALL)
_RUN_TEXT: Final = re.compile(r"<w:t(?: [^>]*)?>([^<]*)</w:t>")


def is_document(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path
    if host == "docs.google.com":
        return bool(_GDOC.match(path))
    if host == "drive.google.com":
        return bool(_DRIVE_FILE.match(path))
    if onedrive.is_share(url):
        return True
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
    where = download_url(url)
    page = (
        fetcher.get(where, max_bytes=DOCUMENT_BYTES)
        if isinstance(fetcher, Fetcher)
        else fetcher.get(where)
    )
    content = page.content
    pages: int | None = None
    if content.startswith(b"%PDF"):
        text, pages = _pdf_text(content, url)
    elif content.startswith(b"PK"):
        text = _docx_text(content, url)
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


def _docx_text(content: bytes, url: str) -> str:
    """The paragraphs of a Word file, one to a line."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > DOCX_BYTES:
                raise FetchError(f"{url} is a Word file too large to read")
            body = archive.read(info).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise FetchError(f"{url} is not a Word file that could be read: {exc}") from exc
    return "\n".join(
        html.unescape("".join(_RUN_TEXT.findall(paragraph)))
        for paragraph in _PARAGRAPH.findall(body)
    )


def document_links(links: list[Link]) -> list[Link]:
    seen: set[str] = set()
    found: list[Link] = []
    for link in links:
        if is_document(link.url) and link.url not in seen:
            seen.add(link.url)
            found.append(link)
    return found
