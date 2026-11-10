"""A city's meeting sources, kept in ``sources.json`` in the city folder.

The desk's Sources screen adds, edits and removes them. The file::

    {
      "format": 1,
      "sources": [
        {"id": "primegov:longmont.primegov.com", "kind": "primegov",
         "url": "https://longmont.primegov.com", "bodies": ["City Council"]},
        {"id": "youtube:UCH5_wkpLrKYb1JuUk6-UdNg", "kind": "youtube",
         "channel_id": "UCH5_wkpLrKYb1JuUk6-UdNg", "bodies": ["City Council"]},
        {"id": "granicus:example.granicus.com/2", "kind": "granicus",
         "host": "example.granicus.com", "view_id": "2", "bodies": ["City Council"]}
      ]
    }

Kinds and what each needs:

- ``primegov``: the portal address, ``https://<name>.primegov.com``.
- ``youtube``: the channel id, ``UC`` and 22 more letters, digits, ``-`` or ``_``.
- ``granicus``: the host, ``<name>.granicus.com``, and the view id (digits).

Every source lists the bodies (council, boards) it covers: at least one name.
The id is made from the kind and its address, so one address is listed once.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

SETTINGS_NAME: Final = "sources.json"
FORMAT: Final = 1
KINDS: Final = ("primegov", "youtube", "granicus")
KIND_LABELS: Final = {
    "primegov": "PrimeGov portal",
    "youtube": "YouTube channel",
    "granicus": "Granicus video archive",
}

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_PRIMEGOV_HOST: Final = re.compile(rf"^{_LABEL}\.primegov\.com$")
_GRANICUS_HOST: Final = re.compile(rf"^{_LABEL}\.granicus\.com$")
_CHANNEL_ID: Final = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_VIEW_ID: Final = re.compile(r"^[0-9]{1,6}$")
_MAX_BODY: Final = 120


class SourceError(ValueError):
    """A source is not valid. ``field`` names the form field the message is about."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Source:
    kind: str
    bodies: tuple[str, ...]
    url: str = ""
    channel_id: str = ""
    host: str = ""
    view_id: str = ""

    @property
    def id(self) -> str:
        if self.kind == "primegov":
            return f"primegov:{urlsplit(self.url).hostname}"
        if self.kind == "youtube":
            return f"youtube:{self.channel_id}"
        return f"granicus:{self.host}/{self.view_id}"

    @property
    def address(self) -> str:
        """What a person reads: the portal, the channel or the archive page."""
        if self.kind == "primegov":
            return self.url
        if self.kind == "youtube":
            return f"https://www.youtube.com/channel/{self.channel_id}"
        return f"https://{self.host}/ViewPublisher.php?view_id={self.view_id}"

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.kind == "primegov":
            data["url"] = self.url
        elif self.kind == "youtube":
            data["channel_id"] = self.channel_id
        else:
            data["host"] = self.host
            data["view_id"] = self.view_id
        data["bodies"] = list(self.bodies)
        return data


def parse_bodies(value: str | Iterable[str]) -> tuple[str, ...]:
    """Body names from a list, or from text with one name per line or commas between names."""
    parts = re.split(r"[,\n]", value) if isinstance(value, str) else list(value)
    seen: list[str] = []
    for part in parts:
        name = " ".join(str(part).split())
        if not name:
            continue
        if len(name) > _MAX_BODY:
            raise SourceError(f"a body name is longer than {_MAX_BODY} characters", "bodies")
        if name.casefold() not in (s.casefold() for s in seen):
            seen.append(name)
    if not seen:
        raise SourceError(
            "name at least one body this source covers, for example City Council", "bodies"
        )
    return tuple(seen)


def _primegov_url(value: str) -> str:
    text = value.strip()
    if not text:
        raise SourceError("enter the PrimeGov portal address", "url")
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise SourceError("the PrimeGov address must start with https://", "url")
    if not _PRIMEGOV_HOST.match(host):
        raise SourceError(
            f"{value.strip()!r} is not a PrimeGov portal; it looks like "
            "https://yourcity.primegov.com",
            "url",
        )
    if parts.port or parts.username or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise SourceError(
            "use the portal's home address only, like https://yourcity.primegov.com", "url"
        )
    return f"https://{host}"


_CHANNEL_URL: Final = re.compile(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})(?:[/?#]|$)")


def _channel_id(value: str) -> str:
    text = value.strip()
    if not text:
        raise SourceError("enter the YouTube channel id", "channel_id")
    found = _CHANNEL_URL.search(text)
    if found:
        text = found.group(1)
    if text.startswith("@") or "youtube.com/@" in text:
        raise SourceError(
            "that is a channel handle; use the channel id instead (it starts with UC and is 24 "
            "characters; YouTube shows it under About > Share channel > Copy channel ID)",
            "channel_id",
        )
    if not _CHANNEL_ID.match(text):
        raise SourceError(
            f"{value.strip()!r} is not a YouTube channel id; it starts with UC and is 24 "
            "letters, digits, - or _",
            "channel_id",
        )
    return text


def _granicus(host_value: str, view_value: str) -> tuple[str, str]:
    text = host_value.strip().lower()
    if not text:
        raise SourceError("enter the Granicus host", "host")
    if "://" in text:
        text = urlsplit(text).hostname or ""
    text = text.split("/")[0]
    if not _GRANICUS_HOST.match(text):
        raise SourceError(
            f"{host_value.strip()!r} is not a Granicus host; it looks like yourcity.granicus.com",
            "host",
        )
    view = view_value.strip()
    if not view:
        raise SourceError("enter the Granicus view id (the number after view_id=)", "view_id")
    if not _VIEW_ID.match(view):
        raise SourceError(f"{view!r} is not a view id; it is a number, like 2", "view_id")
    return text, str(int(view))


def make_source(kind: str, fields: Mapping[str, Any]) -> Source:
    """Check one source's fields and return it, or raise SourceError naming the field."""
    kind = str(kind).strip().lower()
    if kind not in KINDS:
        raise SourceError(f"pick a kind: {', '.join(KINDS)}", "kind")
    # The address is checked before the bodies, in the order the form shows them.
    if kind == "primegov":
        url = _primegov_url(str(fields.get("url") or ""))
        return Source(kind, parse_bodies(fields.get("bodies") or ""), url=url)
    if kind == "youtube":
        channel = _channel_id(str(fields.get("channel_id") or ""))
        return Source(kind, parse_bodies(fields.get("bodies") or ""), channel_id=channel)
    host, view = _granicus(str(fields.get("host") or ""), str(fields.get("view_id") or ""))
    return Source(kind, parse_bodies(fields.get("bodies") or ""), host=host, view_id=view)


def load_sources(city_dir: str | Path) -> list[Source]:
    path = Path(city_dir) / SETTINGS_NAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["sources"]
        if not isinstance(rows, list):
            raise TypeError("sources is not a list")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"{path} is not a readable sources file: {exc}") from exc
    found: list[Source] = []
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise SourceError(f"{path}: source {number} is not an object")
        try:
            source = make_source(str(row.get("kind", "")), row)
        except SourceError as exc:
            raise SourceError(f"{path}: source {number}: {exc}") from exc
        found.append(source)
    _refuse_duplicates(found)
    return found


def save_sources(city_dir: str | Path, sources: list[Source]) -> Path:
    """Write sources.json through a temporary file."""
    _refuse_duplicates(sources)
    path = Path(city_dir) / SETTINGS_NAME
    data = {"format": FORMAT, "sources": [s.to_json() for s in sources]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(tmp, path)
    return path


def _refuse_duplicates(sources: list[Source]) -> None:
    ids = [s.id for s in sources]
    for source_id in ids:
        if ids.count(source_id) > 1:
            raise SourceError(f"{source_id} is listed twice")


def add_source(city_dir: str | Path, source: Source) -> Path:
    sources = load_sources(city_dir)
    if any(s.id == source.id for s in sources):
        raise SourceError(f"{source.address} is already a source; edit it instead")
    return save_sources(city_dir, [*sources, source])


def replace_source(city_dir: str | Path, source_id: str, source: Source) -> Path:
    sources = load_sources(city_dir)
    at = next((n for n, s in enumerate(sources) if s.id == source_id), None)
    if at is None:
        raise SourceError(f"no source {source_id}; it may have been removed")
    if source.id != source_id and any(s.id == source.id for s in sources):
        raise SourceError(f"{source.address} is already a source")
    sources[at] = source
    return save_sources(city_dir, sources)


def remove_source(city_dir: str | Path, source_id: str) -> Path:
    sources = load_sources(city_dir)
    kept = [s for s in sources if s.id != source_id]
    if len(kept) == len(sources):
        raise SourceError(f"no source {source_id}; it may have been removed already")
    return save_sources(city_dir, kept)
