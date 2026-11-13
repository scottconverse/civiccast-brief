"""One city's settings folder, and the ``setup`` command that starts one.

A city folder holds two files:

* ``city.json``: the site and podcast settings the site build reads, plus a ``capture`` object
  that says where the city's meeting videos are: ``{"platform": "youtube"}``,
  ``{"platform": "granicus", "host": "oakland.granicus.com", "view_id": 2}``,
  ``{"platform": "cablecast", "host": "reflect-oakland.cablecast.tv"}`` or ``{"platform": "none"}``
  (no video archive chosen; the meeting command then takes the platform on its command line).
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
PLATFORMS = ("youtube", "granicus", "cablecast", "none")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EMAIL_RE = re.compile(r"^[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+$")
_NAME_MAX = 120


class CityError(ValueError):
    """The city folder is missing a file or a setting; the message says which."""


class CityFieldError(CityError):
    """One value typed for the city is not valid; ``field`` names the form field."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class CityConfig:
    folder: Path
    slug: str
    name: str
    platform: str
    granicus_host: str | None
    granicus_view_id: int | None
    cablecast_host: str | None = None

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
    host = view_id = cablecast_host = None
    if platform == "cablecast":
        cablecast_host = str(capture.get("host") or "").strip()
        if not cablecast_host:
            raise CityError(
                "a Cablecast city needs capture.host, like reflect-oakland.cablecast.tv"
            )
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
        cablecast_host=cablecast_host,
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
    platform: str | None = None,
    cablecast_host: str | None = None,
) -> dict[str, Any]:
    # A city made by Find has no choice yet and gets the long-standing default; ``setup`` always
    # passes the platform it was told or asked for.
    capture: dict[str, Any] = {"platform": platform or ("granicus" if granicus_host else "youtube")}
    if capture["platform"] == "granicus":
        capture.update(host=granicus_host, view_id=granicus_view_id)
    elif capture["platform"] == "cablecast":
        capture["host"] = cablecast_host
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
    platform: str | None = None,
    cablecast_host: str | None = None,
) -> list[Path]:
    """Write a new city folder. Refuses to overwrite a file that is already there.

    The video platform is never assumed: ``platform`` is one of ``PLATFORMS``, or is taken from the
    Granicus or Cablecast host given. With neither, this raises."""
    if not _SLUG_RE.match(slug):
        raise CityError(f"slug {slug!r} must be lowercase letters, digits and single hyphens")
    platform = platform or (
        "granicus" if granicus_host else "cablecast" if cablecast_host else None
    )
    if platform is None:
        raise CityError(
            f"say which video platform the city uses: one of {', '.join(PLATFORMS)} (--platform)"
        )
    if platform not in PLATFORMS:
        raise CityError(f"platform must be one of {', '.join(PLATFORMS)}, not {platform!r}")
    if platform == "granicus" and not granicus_host:
        raise CityError("a Granicus city needs --granicus HOST and --view-id")
    if platform == "cablecast" and not cablecast_host:
        raise CityError(
            "a Cablecast city needs --cablecast HOST, like reflect-oakland.cablecast.tv"
        )
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
            platform=platform,
            cablecast_host=cablecast_host,
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


IDENTITY_FIELDS = ("name", "slug", "state", "contact_email", "podcast_email")


def read_identity(folder: Path) -> dict[str, str]:
    """The city's name, short web name, state and the two email addresses, as the desk's City
    page shows them. A missing city.json gives empty values. The state is ``city.json``'s own
    ``state``, else the one ``roster.json`` carries."""
    folder = Path(folder)
    out = dict.fromkeys(IDENTITY_FIELDS, "")
    path = folder / CITY_FILE
    data = _read(path) if path.exists() else {}
    podcast = data.get("podcast")
    values = {
        "name": data.get("name"),
        "slug": data.get("slug"),
        "state": data.get("state"),
        "contact_email": data.get("contact_email"),
        "podcast_email": podcast.get("owner_email") if isinstance(podcast, dict) else None,
    }
    if not values["state"]:
        try:
            roster = json.loads((folder / ROSTER_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            roster = None
        values["state"] = roster.get("state") if isinstance(roster, dict) else None
    for key, value in values.items():
        if isinstance(value, str):
            out[key] = value.strip()
    return out


def check_identity(values: dict[str, str]) -> dict[str, str]:
    """The values trimmed and checked; raises :class:`CityFieldError` for the first bad one."""
    from civiccast_brief.citybuild.search import STATES, slug_of, state_name

    clean = {key: " ".join(str(values.get(key, "")).split()) for key in IDENTITY_FIELDS}
    clean["slug"] = clean["slug"].replace(" ", "")
    if not clean["name"]:
        raise CityFieldError("Enter the city's name.", "name")
    if len(clean["name"]) > _NAME_MAX:
        raise CityFieldError(f"The name is longer than {_NAME_MAX} characters.", "name")
    if not _SLUG_RE.match(clean["slug"]):
        raise CityFieldError(
            "The short web name must be lowercase letters and digits, with single hyphens "
            "between words, like longmont or fort-collins.",
            "slug",
        )
    state = state_name(clean["state"])
    if not state or slug_of(state) not in set(STATES.values()):
        raise CityFieldError("Enter a US state, like CO or Colorado.", "state")
    clean["state"] = state
    for key, label in (("contact_email", "contact"), ("podcast_email", "podcast")):
        if not _EMAIL_RE.match(clean[key]):
            raise CityFieldError(f"Enter the {label} email address, like name@example.org.", key)
    return clean


def save_identity(folder: Path, values: dict[str, str]) -> Path:
    """Save the checked values into ``city.json``, leaving every other field as it was. With no
    city.json yet, the file is made the way ``setup`` makes it. Returns the file."""
    clean = check_identity(values)
    folder = Path(folder)
    path = folder / CITY_FILE
    if path.exists():
        data = _read(path)
        data["name"] = clean["name"]
        data["slug"] = clean["slug"]
        data["state"] = clean["state"]
        data["contact_email"] = clean["contact_email"]
        podcast = data.get("podcast")
        if not isinstance(podcast, dict):
            podcast = {}
        podcast["owner_email"] = clean["podcast_email"]
        data["podcast"] = podcast
    else:
        data = city_settings(
            slug=clean["slug"],
            name=clean["name"],
            contact_email=clean["contact_email"],
            podcast_email=clean["podcast_email"],
        )
        data["state"] = clean["state"]
    folder.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)
    return path
