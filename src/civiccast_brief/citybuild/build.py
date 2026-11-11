"""Write what a search found into a city folder, as stubs for the editor to confirm.

``city.json`` and ``roster.json`` are written only when the folder has none; a file already there
is never changed. ``sources.json`` gains the found sources, and a source already listed gains the
bodies it lacked. ``city-search.json`` keeps the whole search, so the editor can see how each
source was found and what was found but not saved.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from civiccast_brief.city import CITY_FILE, ROSTER_FILE, city_settings, roster_template
from civiccast_brief.citybuild.search import Search
from civiccast_brief.sources import KIND_LABELS, Source, load_sources, merge_sources

REPORT_FILE: Final = "city-search.json"


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


def build_city(
    folder: str | Path,
    found: Search,
    *,
    chosen: list[str] | None = None,
    slug: str | None = None,
) -> Built:
    """Write the stubs and the chosen sources (every found source when ``chosen`` is None)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    built = Built(folder)
    city = city_settings(
        slug=slug or slug_for(found.name), name=found.name, contact_email="", podcast_email=""
    )
    if _write_new(folder / CITY_FILE, city):
        built.lines.append(f"wrote {folder / CITY_FILE}")
        built.notice.append(f"Wrote {CITY_FILE}.")
        built.to_fill += [
            "city.json contact_email: the address readers write to with corrections",
            "city.json podcast.owner_email: the address podcast directories write to",
        ]
    else:
        built.lines.append(f"kept {folder / CITY_FILE} (already there)")
        built.notice.append(f"Kept {CITY_FILE} (already there).")
    body = (
        "City Council"
        if any(b == "City Council" for b in found.bodies)
        else (found.bodies[0] if found.bodies else "City Council")
    )
    roster = roster_template(name=found.name, state=found.state, body=body, roster_page="")
    if _write_new(folder / ROSTER_FILE, roster):
        built.lines.append(f"wrote {folder / ROSTER_FILE}")
        built.notice.append(f"Wrote {ROSTER_FILE}.")
        built.to_fill.append(f"roster.json members: the {body} members, from the city's page")
    else:
        built.lines.append(f"kept {folder / ROSTER_FILE} (already there)")
        built.notice.append(f"Kept {ROSTER_FILE} (already there).")
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
