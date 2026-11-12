"""Write what a search found into a city folder.

``city.json`` is written only when the folder has none; in a file already there only the email
addresses an earlier build left blank are set. ``roster.json`` is filled from the members page on the city's own site, read by rule
(:mod:`civiccast_brief.citybuild.members`), when the folder has none or has only the empty
template; a roster with members is never changed. ``sources.json`` gains the found sources, and a source already listed gains the
bodies it lacked. ``city-search.json`` keeps the whole search, so the editor can see how each
source was found and what was found but not saved.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final

from civiccast_brief.city import CITY_FILE, ROSTER_FILE, city_settings, roster_template
from civiccast_brief.citybuild import members as members_page
from civiccast_brief.citybuild.fetch import Fetcher, Page
from civiccast_brief.citybuild.search import Search
from civiccast_brief.sources import KIND_LABELS, Source, load_sources, merge_sources

REPORT_FILE: Final = "city-search.json"
#: The address podcast directories write to: the publisher's own, the same for every city
#: (Longmont's city.json has used it since the first episode).
PUBLISHER_EMAIL: Final = "podcast@civiccastbrief.org"
#: The address readers write to, shown on every city's About page. Email Routing forwards it,
#: like the podcast address, to the publisher (set 2026-11-11).
CONTACT_EMAIL: Final = "contact@civiccastbrief.org"


@dataclass
class Built:
    folder: Path
    lines: list[str] = field(default_factory=list)
    to_fill: list[str] = field(default_factory=list)
    notice: list[str] = field(default_factory=list)  # short lines for the desk, no paths


def slug_for(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.casefold())).strip("-") or "city"


def _bodies(n: int) -> str:
    return f"{n} {'body' if n == 1 else 'bodies'}"


def _sources_notice(picked: list[Source], before: set[str]) -> str:
    new = [s for s in picked if s.id not in before]
    kept = len(picked) - len(new)
    parts = [f"{KIND_LABELS.get(s.kind, s.kind)} ({_bodies(len(s.bodies))})" for s in new]
    text = (
        f"Added {len(new)} {'source' if len(new) == 1 else 'sources'}: {', '.join(parts)}."
        if new
        else "No new sources."
    )
    if kept:
        text += f" {kept} {'was' if kept == 1 else 'were'} already saved."
    return text


def _write_new(path: Path, data: object) -> bool:
    if path.exists():
        return False
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def _fill_blank_emails(path: Path) -> list[str]:
    """Set ``contact_email`` and ``podcast.owner_email`` where an earlier build left them blank
    (an empty string). Nothing else changes. Returns the fields set."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    filled = []
    if data.get("contact_email") == "":
        data["contact_email"] = CONTACT_EMAIL
        filled.append("contact_email")
    podcast = data.get("podcast")
    if isinstance(podcast, dict) and podcast.get("owner_email") == "":
        podcast["owner_email"] = PUBLISHER_EMAIL
        filled.append("podcast.owner_email")
    if filled:
        _write(path, data)
    return filled


def _empty_roster(path: Path) -> bool:
    """No roster yet, or only the template with no members."""
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and not data.get("members")


def _write(path: Path, data: object) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _roster(
    built: Built,
    folder: Path,
    found: Search,
    fetcher: Fetcher | None,
    open_in_browser: Callable[[str], Page] | None,
    today: date,
) -> None:
    path = folder / ROSTER_FILE
    if not _empty_roster(path):
        built.lines.append(f"kept {path} (already there)")
        built.notice.append(f"Kept {ROSTER_FILE} (already there).")
        return
    body = members_page.governing_body(found.bodies) or ""
    read: members_page.Found | None = None
    if fetcher is not None and found.website:
        read = members_page.find_members(
            found.website, body, fetcher, open_in_browser=open_in_browser
        )
    if read is not None and read.members:
        _write(path, members_page.roster_from_found(found.name, found.state, read, today))
        built.lines.append(
            f"wrote {path}: {len(read.members)} {read.body} members from {read.page}"
        )
        built.notice.append(f"Wrote {ROSTER_FILE}: {len(read.members)} {read.body} members.")
        return
    body = body or "City Council"
    if not path.exists():
        _write(path, roster_template(name=found.name, state=found.state, body=body, roster_page=""))
        built.lines.append(f"wrote {path} (no members)")
        built.notice.append(f"Wrote {ROSTER_FILE} with no members.")
    why = read.problem if read is not None else "the members page was not looked for"
    if not found.website:
        why = "the city has no website the search found"
    built.lines.append(f"roster: {why}")
    built.to_fill.append(f"roster.json members: the {body} members ({why})")


def build_city(
    folder: str | Path,
    found: Search,
    *,
    chosen: list[str] | None = None,
    slug: str | None = None,
    fetcher: Fetcher | None = None,
    open_in_browser: Callable[[str], Page] | None = None,
    today: date | None = None,
) -> Built:
    """Write the stubs and the chosen sources (every found source when ``chosen`` is None).

    With ``fetcher``, the roster is read from the city's members page; without it (the tests that
    read no pages) an empty roster is written and listed to fill in.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    built = Built(folder)
    city = city_settings(
        slug=slug or slug_for(found.name),
        name=found.name,
        contact_email=CONTACT_EMAIL,
        podcast_email=PUBLISHER_EMAIL,
    )
    if _write_new(folder / CITY_FILE, city):
        built.lines.append(f"wrote {folder / CITY_FILE}")
        built.notice.append(f"Wrote {CITY_FILE}.")
    elif filled := _fill_blank_emails(folder / CITY_FILE):
        built.lines.append(f"set {' and '.join(filled)} in {folder / CITY_FILE} (they were blank)")
        built.notice.append(f"Kept {CITY_FILE}; set its blank email addresses.")
    else:
        built.lines.append(f"kept {folder / CITY_FILE} (already there)")
        built.notice.append(f"Kept {CITY_FILE} (already there).")
    _roster(built, folder, found, fetcher, open_in_browser, today or date.today())
    picked = [c.source for c in found.candidates if chosen is None or c.source.id in set(chosen)]
    before = {s.id for s in load_sources(folder)}
    path, changes = merge_sources(folder, picked)
    built.notice.insert(0, _sources_notice(picked, before))
    built.lines += [f"sources: {change}" for change in changes] or ["sources: nothing new"]
    built.lines.append(f"saved {path}")
    report = folder / REPORT_FILE
    report.write_text(
        json.dumps(found.to_json(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    built.lines.append(f"saved the search to {report}")
    return built
