"""A public Google Drive folder, read through its embedded folder view (no sign-in, no API key)."""

from __future__ import annotations

import html
import re
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Final

from civiccast_brief.citybuild.fetch import Fetcher
from civiccast_brief.citybuild.page import _MONTHS, find_dates, squash

#: The most folders one search walks, so a large archive cannot run on.
MAX_FOLDERS: Final = 40
_ENTRY: Final = re.compile(
    r'<div class="flip-entry" id="entry-([A-Za-z0-9_-]+)".*?<a href="([^"]+)".*?'
    r'<div class="flip-entry-title">(.*?)</div>.*?'
    r'<div class="flip-entry-last-modified"><div>(.*?)</div>',
    re.DOTALL,
)
_MONTH_DAY: Final = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})$")
_TIME: Final = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.IGNORECASE)


def folder_view_url(folder_id: str) -> str:
    return f"https://drive.google.com/embeddedfolderview?id={folder_id}"


@dataclass(frozen=True)
class Entry:
    id: str
    url: str
    title: str
    modified: date | None
    folder: bool


def modified_date(text: str, today: date) -> date | None:
    """Drive prints "Nov 5" for this year, "Nov 5, 2025" or "11/5/25" for older, a time for today."""
    text = squash(text)
    if _TIME.match(text):
        return today
    dates = find_dates(text)
    if dates:
        return dates[0]
    found = _MONTH_DAY.match(text)
    if not found or found[1].lower() not in _MONTHS:
        return None
    try:
        made = date(today.year, _MONTHS[found[1].lower()], int(found[2]))
    except ValueError:
        return None
    return made if made <= today else made.replace(year=today.year - 1)


def entries(page_text: str, today: date) -> list[Entry]:
    found: list[Entry] = []
    for match in _ENTRY.finditer(page_text):
        url = html.unescape(match[2])
        found.append(
            Entry(
                id=match[1],
                url=url,
                title=squash(re.sub(r"<[^>]+>", " ", match[3])),
                modified=modified_date(re.sub(r"<[^>]+>", " ", match[4]), today),
                folder="/drive/folders/" in url or "/folderview" in url,
            )
        )
    return found


def walk(folder_id: str, fetcher: Fetcher, today: date) -> tuple[list[Entry], list[str]]:
    """Every file under a folder, breadth first, and a note for each folder that was not read."""
    queue: deque[str] = deque([folder_id])
    seen: set[str] = set()
    files: list[Entry] = []
    notes: list[str] = []
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        if len(seen) >= MAX_FOLDERS:
            notes.append(f"stopped after {MAX_FOLDERS} folders; {len(queue) + 1} not opened")
            break
        seen.add(current)
        page = fetcher.try_get(folder_view_url(current))
        if page is None:
            notes.append(f"folder {current} could not be opened")
            continue
        for entry in entries(page.text, today):
            if entry.folder:
                queue.append(entry.id)
            else:
                files.append(entry)
    return files, notes
