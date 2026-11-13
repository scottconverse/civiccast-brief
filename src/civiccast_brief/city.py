"""One city's settings folder, and the ``setup`` command that starts one.

A city folder holds two files:

* ``city.json``: the site and podcast settings the site build reads, plus a ``capture`` object
  that says where the city's meeting videos are: ``{"platform": "youtube"}`` or
  ``{"platform": "granicus", "host": "oakland.granicus.com", "view_id": 2}``.
* ``roster.json``: the council members as the city's own page prints their names. It is the only
  source of speaker names. ``setup`` writes it with no members; the weekly run fills them in from each meeting's own
  records (``civiccast_brief.weekly.rosterfill``), and a member added by hand is kept.

``python -m civiccast_brief.meeting run ... --city DIR`` reads the roster and the capture settings
from the folder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CITY_FILE = "city.json"
ROSTER_FILE = "roster.json"
PLATFORMS = ("youtube", "granicus")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class CityError(ValueError):
    """The city folder is missing a file or a setting; the message says which."""


@dataclass(frozen=True)
class CityConfig:
    folder: Path
    slug: str
    name: str
    platform: str
    granicus_host: str | None
    granicus_view_id: int | None

    @property
    def roster_path(self) -> Path:
        return self.folder / ROSTER_FILE


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CityError(f"missing {path}; run `python -m civiccast_brief setup` first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CityError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CityError(f"{path}: the top level must be a JSON object")
    return data


def load_city(folder: Path) -> CityConfig:
    """Read the city folder's settings. The roster is read later by the pipeline."""
    folder = Path(folder)
    data = _read(folder / CITY_FILE)
    capture = data.get("capture") or {"platform": "youtube"}
    if not isinstance(capture, dict):
        raise CityError(f"{folder / CITY_FILE}: capture must be an object")
    platform = str(capture.get("platform", "youtube"))
    if platform not in PLATFORMS:
        raise CityError(f"capture.platform must be one of {', '.join(PLATFORMS)}, not {platform!r}")
    host = view_id = None
    if platform == "granicus":
        host = str(capture.get("host") or "").strip()
        raw_view = capture.get("view_id")
        if not host or not isinstance(raw_view, int):
            raise CityError("a Granicus city needs capture.host and a whole-number capture.view_id")
        view_id = raw_view
    if not (folder / ROSTER_FILE).exists():
        raise CityError(f"missing {folder / ROSTER_FILE}")
    return CityConfig(
        folder=folder,
        slug=str(data.get("slug", "")),
        name=str(data.get("name", "")),
        platform=platform,
        granicus_host=host,
        granicus_view_id=view_id,
    )


def city_settings(
    *,
    slug: str,
    name: str,
    contact_email: str,
    podcast_email: str,
    publisher: str = "CivicCast Brief",
    granicus_host: str | None = None,
    granicus_view_id: int | None = None,
) -> dict[str, Any]:
    capture: dict[str, Any] = {"platform": "youtube"}
    if granicus_host:
        capture = {"platform": "granicus", "host": granicus_host, "view_id": granicus_view_id}
    title = f"CivicCast Brief: {name}"
    return {
        "slug": slug,
        "name": name,
        "site_title": title,
        "tagline": "What did our local government just do?",
        "publisher": publisher,
        "contact_email": contact_email,
        "hosts": {"S1": "Maya", "S2": "Theo"},
        "about": [
            (
                f"Short recaps of {name} public meetings, written from the meeting captions and "
                "read by two AI voices. Every fact links to the moment in the meeting where it was "
                "said. A person approves each episode before it is published."
            )
        ],
        "capture": capture,
        "podcast": {
            "name": title,
            "author": publisher,
            "owner_name": publisher,
            "owner_email": podcast_email,
            "description": f"Short, cited recaps of {name} public meetings, read by two AI hosts.",
            "category": "Government",
            "subcategory": "",
            "language": "en-us",
            "explicit": False,
        },
    }


def roster_template(*, name: str, state: str, body: str, roster_page: str) -> dict[str, Any]:
    return {
        "city": name,
        "state": state,
        "body": body,
        "retrieved": "",
        "sources": [roster_page] if roster_page else [],
        "note": (
            "Nothing to type here. The weekly run fills the members in from each meeting's own "
            "records (the roll call in the minutes, member lists on the agenda), with each "
            "member's source. A member you add by hand stays. Entry format: "
            '{"name": "Jane Doe", "first": "Jane", "last": "Doe", "role": "Mayor"}'
        ),
        "members": [],
    }


def setup(
    folder: Path,
    *,
    slug: str,
    name: str,
    state: str,
    body: str,
    contact_email: str,
    podcast_email: str,
    roster_page: str = "",
    granicus_host: str | None = None,
    granicus_view_id: int | None = None,
) -> list[Path]:
    """Write a new city folder. Refuses to overwrite a file that is already there."""
    if not _SLUG_RE.match(slug):
        raise CityError(f"slug {slug!r} must be lowercase letters, digits and single hyphens")
    if granicus_host and granicus_view_id is None:
        raise CityError("a Granicus city needs --view-id as well as --granicus")
    from civiccast_brief.citybuild.search import state_name

    state = state_name(state)  # "CO" is stored as "Colorado"
    folder = Path(folder)
    targets = {
        folder / CITY_FILE: city_settings(
            slug=slug,
            name=name,
            contact_email=contact_email,
            podcast_email=podcast_email,
            granicus_host=granicus_host,
            granicus_view_id=granicus_view_id,
        ),
        folder / ROSTER_FILE: roster_template(
            name=name, state=state, body=body, roster_page=roster_page
        ),
    }
    existing = [str(p) for p in targets if p.exists()]
    if existing:
        raise CityError(f"already there, not overwritten: {', '.join(existing)}")
    folder.mkdir(parents=True, exist_ok=True)
    for path, data in targets.items():
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return list(targets)
