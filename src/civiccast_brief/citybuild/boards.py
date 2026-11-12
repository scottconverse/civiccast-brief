"""Board rosters from a PrimeGov portal's member lists, for ``rosters/<body>.json``.

The weekly run names a board's speakers from ``rosters/<body>.json`` (see
:func:`civiccast_brief.weekly.run.roster_for`), and a board meeting with no roster is not run. A
PrimeGov portal publishes each committee's members as JSON, the list behind its public Committees
page: each seat's holder, the seat ("Board Member - 4"), the title ("Vice Chair") and the term. The
current holders are read by rule; no model is asked.

Only names and titles are kept. The list also carries each member's email address and phone
number, and those are never stored.

A body's roster that already lists members is not changed, and the governing body keeps
``roster.json`` (read from the city's own members page).
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Final

from civiccast_brief.citybuild.fetch import Fetcher, FetchError
from civiccast_brief.citybuild.members import GOVERNING
from civiccast_brief.citybuild.read import committee_for, committees
from civiccast_brief.sources import Source

ROSTERS_DIR: Final = "rosters"
MEMBERS_PATH: Final = "/api/v1/CommitteeManager/Portal/GetMembers?committeeId={id}"
#: The public page a person opens to see the same members.
COMMITTEES_PAGE: Final = "/public/committees"

_TAG = re.compile(r"<[^>]*>")
_SEAT_NUMBER = re.compile(r"\s*-\s*(?:Alt\s*)?\d+\s*$", re.IGNORECASE)


def slug(text: str) -> str:
    """The file name ``roster_for`` looks for (the same rule as the weekly run's)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _text(value: object) -> str:
    return " ".join(_TAG.sub(" ", html.unescape(str(value or ""))).split())


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _role(row: dict[str, Any]) -> str:
    title = _text(row.get("positionSubtitle"))
    if title:
        return title
    seat = _SEAT_NUMBER.sub("", _text(row.get("positionName")))
    return seat or "Member"


def members_from(rows: object, today: date) -> list[dict[str, str]]:
    """The current members in a GetMembers answer: not vacated, term not ended, in seat order."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("vacated") or row.get("vacatedPosition"):
            continue
        end = _day(row.get("serviceEnd"))
        start = _day(row.get("serviceStart"))
        if (end is not None and end < today) or (start is not None and start > today):
            continue
        person = row.get("person") if isinstance(row.get("person"), dict) else {}
        first, last = _text(person.get("firstName")), _text(person.get("lastName"))
        name = _text(person.get("fullName")) or " ".join(p for p in (first, last) if p)
        if not name or name in seen:
            continue
        seen.add(name)
        found.append({"name": name, "first": first, "last": last, "role": _role(row)})
    return found


def _empty(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and not data.get("members")


def _governing(body: str) -> bool:
    return body.casefold() in {b.casefold() for b in GOVERNING}


def write_primegov_rosters(
    folder: Path, source: Source, city: str, state: str, fetcher: Fetcher, today: date
) -> list[str]:
    """Write ``rosters/<body>.json`` for each of the source's boards the portal lists members
    for. Returns a line for each body."""
    base = source.url.rstrip("/")
    lines: list[str] = []
    try:
        rows = committees(base, fetcher)
    except (FetchError, ValueError) as exc:
        return [f"{base}: the committee list could not be read: {exc}"]
    for body in source.bodies:
        if _governing(body):
            continue
        path = folder / ROSTERS_DIR / f"{slug(body)}.json"
        if not _empty(path):
            lines.append(f"{body}: kept {path.name} (it lists members)")
            continue
        committee = committee_for(body, rows)
        if committee is None:
            lines.append(f"{body}: the portal lists no committee by that name")
            continue
        url = base + MEMBERS_PATH.format(id=committee)
        try:
            members = members_from(fetcher.get(url).json(), today)
        except (FetchError, ValueError) as exc:
            lines.append(f"{body}: the member list could not be read: {exc}")
            continue
        if not members:
            lines.append(f"{body}: the portal lists no current members")
            continue
        roster = {
            "city": city,
            "state": state,
            "body": body,
            "retrieved": today.isoformat(),
            "sources": [base + COMMITTEES_PAGE],
            "note": (
                "Current members (term not ended, seat not vacated) as the city's PrimeGov "
                f"portal lists them, read by rule (no model) on {today.isoformat()}."
            ),
            "members": members,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(roster, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        lines.append(f"{body}: wrote {path.name}, {len(members)} members")
    return lines


# ----- A school board's members page -----------------------------------------------------------

#: A school board page prints each director's district under the name: "District: E, Term:
#: 2023 - 2029" (St. Vrain Valley's) or "President, District B, Term Expires: 2029" (Boulder
#: Valley's), each seen 2026-11-11.
_OFFICE = (
    r"(?:Assistant\s+Secretary|Vice[\s-]President|President|Secretary|Treasurer|Member|Director)"
)
_DISTRICT_LINE = re.compile(
    rf"^(?:(?P<office>{_OFFICE})\s*,\s*)?District:?\s+(?P<district>[A-Z0-9]{{1,2}})\b(?:\s*[,;].*)?$",
    re.IGNORECASE,
)
_OFFICE_LINE = re.compile(rf"^{_OFFICE}$", re.IGNORECASE)
#: Link words that lead from a board's meetings page to its members.
_MEMBER_LINK = re.compile(r"meet the board|board members|our board|\bmembers\b", re.IGNORECASE)
#: Pages read at most for one body.
PAGES_PER_BODY: Final = 6


def _office(text: str) -> str:
    office = " ".join(text.split()).title().replace("Vice-President", "Vice President")
    return "Board Member" if office in ("", "Member", "Director") else office


def read_district_members(text: str) -> list[dict[str, str]]:
    """The directors a school board page prints, each with the district under the name."""
    from civiccast_brief.citybuild.members import as_name, page_lines, split_name

    lines = page_lines(text)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = _DISTRICT_LINE.match(line)
        if not match or index == 0:
            continue
        above = index - 1
        name = as_name(lines[above])
        if name is None and index >= 2 and len(lines[above].split()) == 1:
            above -= 1  # "Jacqueline" and "Weiss" on two lines
            name = as_name(f"{lines[above]} {lines[above + 1]}")
        if name is None or name in seen:
            continue
        office = match["office"] or ""
        if not office and above >= 1 and _OFFICE_LINE.match(lines[above - 1]):
            office = lines[above - 1]
        seen.add(name)
        first, last = split_name(name)
        role = f"{_office(office)}, District {match['district'].upper()}"
        found.append({"name": name, "first": first, "last": last, "role": role})
    return found


_LINKED_FROM = re.compile(r"linked from (https?://\S+)")


def start_pages(folder: Path, sources: list[Source]) -> dict[str, list[str]]:
    """Each body's own web pages: a page source's address, and the page a source was found on
    (``city-search.json`` says "linked from <page>")."""
    pages: dict[str, list[str]] = {}
    for source in sources:
        if source.kind == "webpage" and source.url:
            for body in source.bodies:
                pages.setdefault(body, []).append(source.url)
    try:
        search = json.loads((folder / "city-search.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        search = {}
    for candidate in search.get("sources", []) if isinstance(search, dict) else []:
        how = str(candidate.get("how", "")) if isinstance(candidate, dict) else ""
        match = _LINKED_FROM.search(how)
        if not match:
            continue
        url = match[1].rstrip(".,;)")
        for body in candidate.get("source", {}).get("bodies", []):
            if url not in pages.get(body, []):
                pages.setdefault(body, []).append(url)
    return pages


def _parents(url: str) -> list[str]:
    head, _, path = url.partition("://")
    host, _, rest = path.partition("/")
    parts = [p for p in rest.split("?", 1)[0].split("/") if p]
    return [f"{head}://{host}/" + "/".join(parts[:n]) + "/" for n in range(len(parts) - 1, 1, -1)]


def find_board_members(
    starts: list[str], fetcher: Fetcher, pause: Any = None
) -> tuple[list[dict[str, str]], str]:
    """The members on the best page reached from ``starts``: each start page, the pages above it,
    and the links on them that lead to members. Returns (members, page)."""
    from civiccast_brief.citybuild.page import parse

    queue = list(dict.fromkeys(u for s in starts for u in [s, *_parents(s)]))
    seen: set[str] = set()
    best: tuple[list[dict[str, str]], str] = ([], "")
    read = 0
    while queue and read < PAGES_PER_BODY:
        url = queue.pop(0)
        if url.rstrip("/") in seen:
            continue
        seen.add(url.rstrip("/"))
        if read and pause is not None:
            pause()
        read += 1
        page = fetcher.try_get(url)
        if page is None:
            continue
        members = read_district_members(page.text)
        if len(members) > len(best[0]):
            best = (members, page.url)
        _, links, _ = parse(page.text, page.url)
        host = url.split("/")[2]
        for link in links:
            same_site = link.url.split("/")[2:3] == [host]
            if _MEMBER_LINK.search(link.text) and same_site and link.url.rstrip("/") not in seen:
                queue.insert(0, link.url)
    return best if len(best[0]) >= 3 else ([], "")


def write_page_rosters(
    folder: Path,
    sources: list[Source],
    city: str,
    state: str,
    fetcher: Fetcher,
    today: date,
    pause: Any = None,
) -> list[str]:
    """Write ``rosters/<body>.json`` for each body whose own pages print its members by district
    (a school board's). Returns a line for each body tried."""
    lines: list[str] = []
    for body, starts in start_pages(folder, sources).items():
        path = folder / ROSTERS_DIR / f"{slug(body)}.json"
        if _governing(body) or not _empty(path):
            continue
        members, page = find_board_members(starts, fetcher, pause)
        if not members:
            lines.append(f"{body}: no members page found from {', '.join(starts)}")
            continue
        roster = {
            "city": city,
            "state": state,
            "body": body,
            "retrieved": today.isoformat(),
            "sources": [page],
            "note": (
                "Names and offices as the board's page prints them, read by rule (no model) on "
                f"{today.isoformat()} from the page in sources."
            ),
            "members": members,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(roster, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        lines.append(f"{body}: wrote {path.name}, {len(members)} members from {page}")
    return lines


def copy_shared_rosters(folder: Path, sources: list[Source]) -> list[str]:
    """A board several towns share (a school district's, on one channel) has one roster: a body
    with no roster gets the one a sibling city folder wrote for a source with the same id."""
    from civiccast_brief.sources import SourceError, load_sources

    lines: list[str] = []
    wanted = {(s.id, b) for s in sources for b in s.bodies if not _governing(b)}
    for other in sorted(p for p in folder.parent.iterdir() if p.is_dir() and p != folder):
        try:
            theirs = load_sources(other)
        except (SourceError, OSError):
            continue
        for source in theirs:
            for body in source.bodies:
                path = folder / ROSTERS_DIR / f"{slug(body)}.json"
                found = other / ROSTERS_DIR / f"{slug(body)}.json"
                if (source.id, body) not in wanted or not _empty(path) or _empty(found):
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(found.read_bytes())
                lines.append(f"{body}: copied {path.name} from {other.name} (the same {source.id})")
    return lines
