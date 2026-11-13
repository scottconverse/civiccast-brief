"""Fill a body's roster from the meeting's own public records, during the weekly run.

A person setting up a city does not type the council or a board into ``roster.json`` or
``rosters/<body>.json``. For each meeting the run reads the member names and titles from that
meeting's documents (:mod:`civiccast_brief.meeting.rollcall`: the roll call of the minutes in the
packet, a member list on the agenda) by plain parsing, no model, and merges them into the body's
roster file before the recap names any speaker:

* every member keeps where the record printed them: the document, its URL with the page, the line
  on the page, the date of the meeting it was read from, and the sentence;
* a member already in the file is not changed: only the date seen and the source are added; a
  member nobody lists in the records is never removed (hand entries stay);
* a name no record prints in full is not added, and when no record lists the members the roster is
  left as it is and the run says so.

The file format stays the one :func:`civiccast_brief.meeting.roster.load_roster` reads (``name``,
``first``, ``last``, ``role``); the added keys (``seen``, ``records``) are for people reading it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from civiccast_brief.meeting import rollcall

#: The note a roster file made from records starts with.
CREATED_NOTE = (
    "Members read from the meeting records (roll call and member lists in the agenda, packet and "
    "minutes) by the weekly run, by rule and with no model. Each member's records say where."
)


def _key(name: str) -> tuple[str, str]:
    words = name.split()
    return (words[0].casefold(), words[-1].casefold()) if words else ("", "")


def _firsts(name: str) -> set[str]:
    """The first names a listed name can be known by: its first word and a nickname in quotes
    (``Liang "Warren" Wang`` is Liang and Warren)."""
    words = name.replace("“", '"').replace("”", '"').split()
    out = {words[0].casefold()} if words else set()
    out.update(
        w.strip('"').casefold() for w in words[1:-1] if w.startswith('"') and w.endswith('"')
    )
    return out


def _same_person(listed: str, printed: str) -> bool:
    """Whether a record's name and a name already in the roster are one person: the same
    surname, and a first name that is the same, a nickname in quotes, or begins the same way for
    three letters or is the start of the other ("Andy" and "Andrew H", "Jeff" and "Jeffrey M")."""
    a, b = listed.split(), printed.split()
    if not a or not b or a[-1].casefold() != b[-1].casefold():
        return False
    wanted = b[0].casefold()
    for first in _firsts(listed):
        if first == wanted or first.startswith(wanted) or wanted.startswith(first):
            return True
        if len(first) >= 3 and first[:3] == wanted[:3]:
            return True
    return False


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _skeleton(city: Path, body: str, day: date) -> dict[str, Any]:
    """A new roster file's head: the city's name and state from its own files."""
    name, state = "", ""
    for file in ("roster.json", "city.json"):
        data = _read(city / file) or {}
        name = name or str(data.get("city") or data.get("name") or "")
        state = state or str(data.get("state") or "")
    return {
        "city": name,
        "state": state,
        "body": body,
        "retrieved": day.isoformat(),
        "sources": [],
        "note": CREATED_NOTE,
        "members": [],
    }


def _record(sighting: rollcall.Sighting) -> dict[str, Any]:
    where = sighting.url
    if sighting.page:
        where = f"{sighting.url}#page={sighting.page}"
    return {
        "date": sighting.held,
        "status": sighting.status,
        "title": sighting.role,
        "document": sighting.document,
        "url": where,
        "page": sighting.page,
        "line": sighting.line,
        "text": sighting.text,
    }


def merge(roster: dict[str, Any], sightings: list[rollcall.Sighting]) -> tuple[list[str], int]:
    """Add ``sightings`` to ``roster`` (a roster file's data), in place. Returns the names added
    and how many members already listed gained a record or a date."""
    members = roster.setdefault("members", [])
    if not isinstance(members, list):
        raise TypeError("members is not a list")
    listed = [m for m in members if isinstance(m, dict)]
    index = {_key(str(m.get("name", ""))): m for m in listed}
    added: list[str] = []
    touched: set[tuple[str, str]] = set()
    for sighting in sightings:
        key = _key(sighting.name)
        member = index.get(key) or next(
            (m for m in listed if _same_person(str(m.get("name", "")), sighting.name)), None
        )
        if member is not None and member.get("name") != sighting.name:
            printed = member.setdefault("printed_as", [])
            if sighting.name not in printed:
                printed.append(sighting.name)
                touched.add(_key(str(member.get("name", ""))))
        if member is None:
            member = {
                "name": sighting.name,
                "first": sighting.first,
                "last": sighting.last,
                "role": sighting.role,
            }
            members.append(member)
            listed.append(member)
            index[key] = member
            added.append(sighting.name)
        record = _record(sighting)
        records = member.setdefault("records", [])
        seen = member.setdefault("seen", [])
        same = (record["date"], record["url"], record["line"])
        if not any((r.get("date"), r.get("url"), r.get("line")) == same for r in records):
            records.append(record)
            if sighting.name not in added:
                touched.add(key)
        if sighting.held not in seen:
            seen.append(sighting.held)
            seen.sort()
            if sighting.name not in added:
                touched.add(key)
    sources = roster.setdefault("sources", [])
    if isinstance(sources, list):
        for sighting in sightings:
            if sighting.url and sighting.url not in sources:
                sources.append(sighting.url)
    return added, len(touched)


def fill_roster(
    path: Path,
    city: Path,
    body: str,
    day: date,
    documents: Mapping[str, Any] | None,
    agenda_text: str = "",
) -> list[str]:
    """Read the meeting's records and merge the members into the roster file at ``path`` (made
    when it is not there). Returns the lines for the run's report."""
    pages = documents.get("documents", []) if documents else []
    found = rollcall.read_documents(pages, body, day) if pages else None
    sightings = list(found.sightings) if found else []
    if agenda_text:
        sightings += rollcall.read_agenda(agenda_text, body, day)
    lines: list[str] = []
    if found and found.surname_only:
        lines.append(
            f"roster {body}: not added, the records print only the surname: "
            + ", ".join(found.surname_only)
        )
    if not sightings:
        return [f"no member list found in the records for {body} {day.isoformat()}", *lines]
    data = _read(path) if path.exists() else _skeleton(city, body, day)
    if data is None:
        return [f"roster {body}: {path.name} is not readable JSON; left as it is", *lines]
    try:
        added, touched = merge(data, sightings)
    except (ValueError, TypeError) as exc:
        return [f"roster {body}: {path.name} left as it is ({exc})", *lines]
    names = sorted({s.name for s in sightings})
    changed = bool(added or touched)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    docs = ", ".join(
        sorted({f"{s.document} p.{s.page}" if s.page else s.document for s in sightings})
    )
    lines.insert(
        0,
        f"roster {body}: {len(names)} members read from the records ({docs}); "
        f"{len(added)} added ({', '.join(added) or 'none'}), {len(names) - len(added)} already listed"
        + ("" if changed else "; nothing new to write"),
    )
    return lines


def loaded_rosters(city: Path) -> list[dict[str, Any]]:
    """Every roster file in the city's folder, for the desk's read-only roster page: the body and,
    for each member, the title and where the records print them."""
    paths = [city / "roster.json", *sorted((city / "rosters").glob("*.json"))]
    out: list[dict[str, Any]] = []
    for path in paths:
        data = _read(path) if path.is_file() else None
        if data is None:
            continue
        members = []
        for member in data.get("members") or []:
            if not isinstance(member, dict):
                continue
            records = [r for r in member.get("records", []) if isinstance(r, dict)]
            members.append(
                {
                    "name": str(member.get("name", "")),
                    "role": str(member.get("role", "")),
                    "seen": [str(s) for s in member.get("seen", [])],
                    "records": records,
                }
            )
        out.append(
            {
                "file": path.name,
                "body": str(data.get("body", "")),
                "retrieved": str(data.get("retrieved", "")),
                "sources": [str(s) for s in data.get("sources", [])],
                "members": members,
            }
        )
    return out
