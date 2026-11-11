"""Links and dates on a web page, read with rules and no model.

A meeting page lists documents as links. Its date is in the link's own text ("Board of Directors
Meeting Agenda 2026-11-03"), in the text just after it (a list that prints the date under the
title), or in the file name ("Report 11_03_2026.pdf"). :func:`links` keeps each link with the text
around it so that :func:`link_date` can look in that order.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Final
from urllib.parse import unquote, urljoin, urlsplit

_MONTHS: Final = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH_NAME: Final = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "November 3, 2026", "Nov. 3 2026", "November 3rd, 2026".
_LONG: Final = re.compile(
    rf"\b({_MONTH_NAME})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE
)
# "3 November 2026".
_DAY_FIRST: Final = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_NAME})\.?,?\s+(\d{{4}})\b", re.IGNORECASE
)
_ISO: Final = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# "11/5/26", "11/05/2026", "11-05-2026", "11_03_2026", "11.11.26".
_NUMERIC: Final = re.compile(r"(?<![\d/._-])(\d{1,2})([/._-])(\d{1,2})\2(\d{4}|\d{2})(?![\d/])")

#: How much text after a link still belongs to it, when the next link does not come first.
AFTER_CHARS: Final = 160
BEFORE_CHARS: Final = 160


def _make(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000
    if not 1990 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    """Every date written in ``text``, in the order they appear."""
    found: list[tuple[int, date]] = []
    for match in _LONG.finditer(text):
        made = _make(int(match[3]), _MONTHS[match[1].lower()], int(match[2]))
        if made:
            found.append((match.start(), made))
    for match in _DAY_FIRST.finditer(text):
        made = _make(int(match[3]), _MONTHS[match[2].lower()], int(match[1]))
        if made:
            found.append((match.start(), made))
    for match in _ISO.finditer(text):
        made = _make(int(match[1]), int(match[2]), int(match[3]))
        if made:
            found.append((match.start(), made))
    for match in _NUMERIC.finditer(text):
        made = _make(int(match[4]), int(match[1]), int(match[3]))
        if made:
            found.append((match.start(), made))
    found.sort(key=lambda pair: pair[0])
    return [made for _, made in found]


def first_date(text: str) -> date | None:
    dates = find_dates(text)
    return dates[0] if dates else None


@dataclass(frozen=True)
class Link:
    url: str
    text: str
    #: Text after the link, up to the next link or :data:`AFTER_CHARS`.
    after: str = ""
    #: Text before the link, back to the previous link or :data:`BEFORE_CHARS`.
    before: str = ""

    @property
    def file_name(self) -> str:
        return unquote(urlsplit(self.url).path.rsplit("/", 1)[-1])


def link_date(link: Link) -> date | None:
    """The date a link is about: its text, then the text after it, the file name, the text before."""
    for text in (link.text, link.after, link.file_name, link.before[-BEFORE_CHARS:]):
        found = first_date(text)
        if found:
            return found
    return None


def squash(text: str) -> str:
    return " ".join(html.unescape(text).split())


class _Links(HTMLParser):
    """Collects links, iframes and the title. Text outside links is kept per gap between links:
    gap ``n`` is the text after link ``n - 1`` and before link ``n``."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title: list[str] = []
        self.urls: list[str] = []
        self.texts: list[list[str]] = []
        self.gaps: list[list[str]] = [[]]
        self.frames: list[str] = []
        self._in_link = False
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and values.get("href"):
            self._end_link()
            self.urls.append(urljoin(self.base, values["href"].strip()))
            self.texts.append([])
            self._in_link = True
        elif tag == "iframe" and values.get("src"):
            self.frames.append(urljoin(self.base, values["src"].strip()))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a":
            self._end_link()

    def _end_link(self) -> None:
        if self._in_link:
            self._in_link = False
            self.gaps.append([])

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
        elif self._in_link:
            self.texts[-1].append(data)
        else:
            self.gaps[-1].append(data)


def parse(text: str, base: str) -> tuple[str, list[Link], list[str]]:
    """The page title, its links with the text around them, and its iframe addresses."""
    parser = _Links(base)
    parser.feed(text)
    parser.close()
    parser._end_link()
    found: list[Link] = []
    for index, url in enumerate(parser.urls):
        before = squash(" ".join(parser.gaps[index]))[-BEFORE_CHARS:]
        after_gap = parser.gaps[index + 1] if index + 1 < len(parser.gaps) else []
        after = squash(" ".join(after_gap))[:AFTER_CHARS]
        found.append(
            Link(url=url, text=squash(" ".join(parser.texts[index])), after=after, before=before)
        )
    return squash(" ".join(parser.title)), found, parser.frames


def page_text(text: str) -> str:
    """The readable text of a page, scripts and tags removed."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    return squash(re.sub(r"<[^>]+>", " ", text))
