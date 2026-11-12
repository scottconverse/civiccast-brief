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
