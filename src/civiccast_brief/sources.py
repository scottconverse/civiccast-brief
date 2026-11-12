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
         "host": "example.granicus.com", "view_id": "2", "bodies": ["City Council"]},
        {"id": "webpage:https://www.svvsd.org/about/board-of-education/board-meetings/",
         "kind": "webpage", "url": "https://www.svvsd.org/about/board-of-education/board-meetings/",
         "follow": "/boardmeetings/", "bodies": ["Board of Education"]},
        {"id": "gdrive:15YuyqNfAkNPcbaoT8r6SSedKGyZ6Box2", "kind": "gdrive",
         "folder_id": "15YuyqNfAkNPcbaoT8r6SSedKGyZ6Box2", "bodies": ["Board of Directors"]},
        {"id": "civicclerk:erieco", "kind": "civicclerk", "tenant": "erieco",
         "bodies": ["Planning Commission"]},
        {"id": "escribe:bouldercounty", "kind": "escribe", "tenant": "bouldercounty",
         "bodies": ["Board of County Commissioners"]},
        {"id": "civicplus:www.townoflyons.com", "kind": "civicplus",
         "site": "www.townoflyons.com", "bodies": ["Board of Trustees"]},
        {"id": "laserfiche:https://laserfiche.louisvilleco.gov/WebLink/Browse.aspx?id=8986&dbid=0&repo=Louisville",
         "kind": "laserfiche",
         "url": "https://laserfiche.louisvilleco.gov/WebLink/Browse.aspx?id=8986&dbid=0&repo=Louisville",
         "bodies": ["Planning Commission"]}
      ]
    }

Kinds and what each needs:

- ``primegov``: the portal address, ``https://<name>.primegov.com``.
- ``youtube``: the channel id, ``UC`` and 22 more letters, digits, ``-`` or ``_``.
- ``granicus``: the host, ``<name>.granicus.com``, and the view id (digits).
- ``webpage``: a page that links the meeting documents (agendas, packets, minutes), for a body
  with no meeting platform, like a school board. ``follow`` is optional: the start of a path, like
  ``/boardmeetings/``, for a page that links one page per meeting instead of the documents.
- ``gdrive``: a public Google Drive folder of meeting documents, by its folder id.
- ``civicclerk``: a CivicClerk portal, by its tenant: ``erieco`` in
  ``https://erieco.portal.civicclerk.com``.
- ``escribe``: an eScribe meetings site, by its tenant: ``bouldercounty`` in
  ``https://pub-bouldercounty.escribemeetings.com``.
- ``civicplus``: a CivicPlus town site with an Agenda Center or an Archive Center, by its host:
  ``www.townoflyons.com``.
- ``laserfiche``: one body's folder in a Laserfiche WebLink records site, by its Browse.aspx
  address. These sites open only in a browser, so the folder is read in the installed Google
  Chrome (see :mod:`civiccast_brief.citybuild.browser`).

Every source lists the bodies (council, boards) it covers: at least one name.
The id is made from the kind and its address, so one address is listed once.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

SETTINGS_NAME: Final = "sources.json"
FORMAT: Final = 1
KINDS: Final = (
    "primegov",
    "youtube",
    "granicus",
    "webpage",
    "gdrive",
    "civicclerk",
    "escribe",
    "civicplus",
    "laserfiche",
    "connector",
)
KIND_LABELS: Final = {
    "primegov": "PrimeGov portal",
    "youtube": "YouTube channel",
    "granicus": "Granicus video archive",
    "webpage": "Web page of meeting documents",
    "gdrive": "Google Drive folder",
    "civicclerk": "CivicClerk portal",
    "escribe": "eScribe meetings site",
    "civicplus": "CivicPlus Agenda Center or Archive Center",
    "laserfiche": "Laserfiche WebLink folder (read in Chrome)",
    "connector": "Standalone civic connector (31 providers)",
}

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_PRIMEGOV_HOST: Final = re.compile(rf"^{_LABEL}\.primegov\.com$")
_GRANICUS_HOST: Final = re.compile(rf"^{_LABEL}\.granicus\.com$")
_CHANNEL_ID: Final = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_VIEW_ID: Final = re.compile(r"^[0-9]{1,6}$")
_FOLDER_ID: Final = re.compile(r"^[A-Za-z0-9_-]{10,100}$")
_CLERK_HOST: Final = re.compile(rf"^({_LABEL})\.(?:portal|api)\.civicclerk\.com$")
_ESCRIBE_HOST: Final = re.compile(rf"^pub-({_LABEL})\.escribemeetings\.com$")
_TENANT: Final = re.compile(rf"^{_LABEL}$")
_SITE: Final = re.compile(rf"^(?:{_LABEL}\.)+[a-z]{{2,24}}$")
_FOLLOW: Final = re.compile(r"^/[A-Za-z0-9._~%/-]{1,200}$")
_MAX_URL: Final = 500
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
    follow: str = ""
    folder_id: str = ""
    #: The CivicClerk or eScribe tenant.
    tenant: str = ""
    #: The CivicPlus site's host.
    site: str = ""
    provider: str = ""
    input_format: str = ""
    capture: str = ""
    namespace: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        if self.kind == "connector":
            return f"connector:{self.provider}:{self.url}"
        if self.kind == "primegov":
            return f"primegov:{urlsplit(self.url).hostname}"
        if self.kind == "youtube":
            return f"youtube:{self.channel_id}"
        if self.kind in ("webpage", "laserfiche"):
            return f"{self.kind}:{self.url}"
        if self.kind == "gdrive":
            return f"gdrive:{self.folder_id}"
        if self.kind in ("civicclerk", "escribe"):
            return f"{self.kind}:{self.tenant}"
        if self.kind == "civicplus":
            return f"civicplus:{self.site}"
        return f"granicus:{self.host}/{self.view_id}"

    @property
    def address(self) -> str:
        """What a person reads: the portal, the channel, the page or the folder."""
        if self.kind in ("primegov", "webpage", "laserfiche", "connector"):
            return self.url
        if self.kind == "youtube":
            return f"https://www.youtube.com/channel/{self.channel_id}"
        if self.kind == "gdrive":
            return f"https://drive.google.com/drive/folders/{self.folder_id}"
        if self.kind == "civicclerk":
            return f"https://{self.tenant}.portal.civicclerk.com"
        if self.kind == "escribe":
            return f"https://pub-{self.tenant}.escribemeetings.com"
        if self.kind == "civicplus":
            return f"https://{self.site}"
        return f"https://{self.host}/ViewPublisher.php?view_id={self.view_id}"

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.kind == "connector":
            data.update(url=self.url, provider=self.provider, input_format=self.input_format)
            if self.capture:
                data["capture"] = self.capture
            if self.options:
                data["options"] = self.options
            if self.namespace:
                data["namespace"] = self.namespace
        elif self.kind == "primegov":
            data["url"] = self.url
        elif self.kind == "youtube":
            data["channel_id"] = self.channel_id
        elif self.kind == "webpage":
            data["url"] = self.url
            if self.follow:
                data["follow"] = self.follow
        elif self.kind == "gdrive":
            data["folder_id"] = self.folder_id
        elif self.kind in ("civicclerk", "escribe"):
            data["tenant"] = self.tenant
        elif self.kind == "civicplus":
            data["site"] = self.site
        elif self.kind == "laserfiche":
            data["url"] = self.url
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


def _webpage(url_value: str, follow_value: str) -> tuple[str, str]:
    text = url_value.strip()
    if not text:
        raise SourceError(
            "enter the address of the page that lists the meeting documents", "page_url"
        )
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise SourceError("the page address must start with https://", "page_url")
    if parts.username or parts.password or parts.port:
        raise SourceError("use the page's plain address, with no user name or port", "page_url")
    if len(text) > _MAX_URL or any(c.isspace() for c in text):
        raise SourceError("that page address is too long or has spaces in it", "page_url")
    text = text.split("#", 1)[0]
    follow = follow_value.strip()
    if follow and not _FOLLOW.match(follow):
        raise SourceError(
            "follow is the start of the meeting pages' path, like /boardmeetings/, or empty",
            "follow",
        )
    return text, follow


def _folder_id(value: str) -> str:
    text = value.strip()
    if not text:
        raise SourceError("enter the Google Drive folder's address or id", "folder_id")
    if "drive.google.com" in text:
        parts = urlsplit(text if "://" in text else f"https://{text}")
        if "embeddedfolderview" in parts.path:
            text = (parse_qs(parts.query).get("id") or [""])[0]
        else:
            text = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if not _FOLDER_ID.match(text):
        raise SourceError(
            f"{value.strip()!r} is not a Google Drive folder; paste its address, like "
            "https://drive.google.com/drive/folders/...",
            "folder_id",
        )
    return text


def _tenant(value: str, host: re.Pattern[str], field: str, example: str) -> str:
    text = value.strip().lower()
    if not text:
        raise SourceError(f"enter the tenant or the site's address, like {example}", field)
    if "." in text or "/" in text:
        name = (urlsplit(text if "://" in text else f"https://{text}").hostname or "").lower()
        match = host.match(name)
        if not match:
            raise SourceError(
                f"{value.strip()!r} is not that kind of site; it looks like {example}", field
            )
        return match.group(1)
    if not _TENANT.match(text):
        raise SourceError(f"{value.strip()!r} is not a tenant name; it looks like {example}", field)
    return text


def _site(value: str) -> str:
    text = value.strip().lower()
    if not text:
        raise SourceError("enter the town site's address, like https://www.townoflyons.com", "site")
    name = (urlsplit(text if "://" in text else f"https://{text}").hostname or "").lower()
    if not _SITE.match(name):
        raise SourceError(
            f"{value.strip()!r} is not a site address; it looks like https://www.townoflyons.com",
            "site",
        )
    return name


def _laserfiche_url(value: str) -> str:
    from civiccast_brief.citybuild.laserfiche import LaserficheError, browse_url, parse_url

    text = value.strip()
    if not text:
        raise SourceError(
            "enter the address of the body's WebLink folder, ending in Browse.aspx?id=...",
            "laserfiche_url",
        )
    if len(text) > _MAX_URL or any(c.isspace() for c in text):
        raise SourceError("that folder address is too long or has spaces in it", "laserfiche_url")
    try:
        host, base, folder, repo = parse_url(text)
    except LaserficheError as exc:
        raise SourceError(str(exc), "laserfiche_url") from exc
    if not _SITE.match(host) or not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*", base):
        raise SourceError(f"{value.strip()!r} is not a WebLink folder address", "laserfiche_url")
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,100}", repo):
        raise SourceError(
            "the repository name (repo=...) has letters it cannot have", "laserfiche_url"
        )
    return browse_url(host, base, folder, repo)


def make_source(kind: str, fields: Mapping[str, Any]) -> Source:
    """Check one source's fields and return it, or raise SourceError naming the field."""
    kind = str(kind).strip().lower()
    if kind not in KINDS:
        raise SourceError(f"pick a kind: {', '.join(KINDS)}", "kind")
    if kind == "connector":
        from civic_data_connectors import create_engine
        from civic_data_connectors.providers import FORMATS
        from civic_data_connectors.providers.identification import ADDITIONAL_FORMATS

        provider = str(fields.get("provider") or "")
        if provider not in FORMATS:
            raise SourceError("pick a provider from the standalone capability list", "provider")
        url = str(fields.get("connector_url") or fields.get("url") or "").strip()
        input_format = str(fields.get("input_format") or "")
        if input_format and input_format not in (
            FORMATS[provider] + ADDITIONAL_FORMATS.get(provider, ())
        ):
            raise SourceError("pick this provider's documented saved format", "input_format")
        options = fields.get("options") or {}
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except ValueError as exc:
                raise SourceError("options must be a JSON object", "options") from exc
        if not isinstance(options, dict):
            raise SourceError("options must be a JSON object", "options")
        namespace = str(fields.get("namespace") or "").strip()
        result = create_engine().execute(
            "validate_source",
            {
                "source": {
                    "provider": provider,
                    "url": url,
                    "options": options,
                    **({"namespace": namespace} if namespace else {}),
                }
            },
        )
        if result.status not in {"completed", "partial"}:
            raise SourceError(
                "; ".join(e.message for e in result.errors) or "invalid configured source",
                "connector_url",
            )
        capture = str(fields.get("capture") or "")
        if capture and (
            Path(capture).is_absolute()
            or "\\" in capture
            or ":" in capture
            or any(part in {"..", "."} for part in capture.split("/"))
        ):
            raise SourceError("capture must be a relative file inside the city folder", "capture")
        return Source(
            kind,
            parse_bodies(fields.get("bodies") or ""),
            url=url,
            provider=provider,
            input_format=input_format,
            capture=capture,
            namespace=namespace,
            options=options,
        )
    # The address is checked before the bodies, in the order the form shows them.
    if kind == "primegov":
        url = _primegov_url(str(fields.get("url") or ""))
        return Source(kind, parse_bodies(fields.get("bodies") or ""), url=url)
    if kind == "youtube":
        channel = _channel_id(str(fields.get("channel_id") or ""))
        return Source(kind, parse_bodies(fields.get("bodies") or ""), channel_id=channel)
    if kind == "webpage":
        # The desk form calls the field page_url, beside PrimeGov's url; the file calls it url.
        page = fields.get("page_url") or fields.get("url") or ""
        url, follow = _webpage(str(page), str(fields.get("follow") or ""))
        return Source(kind, parse_bodies(fields.get("bodies") or ""), url=url, follow=follow)
    if kind == "gdrive":
        folder = _folder_id(str(fields.get("folder_id") or ""))
        return Source(kind, parse_bodies(fields.get("bodies") or ""), folder_id=folder)
    if kind == "civicclerk":
        # The desk form names each kind's tenant field apart; the file calls it tenant.
        value = str(fields.get("clerk_tenant") or fields.get("tenant") or "")
        tenant = _tenant(
            value, _CLERK_HOST, "clerk_tenant", "https://yourtown.portal.civicclerk.com"
        )
        return Source(kind, parse_bodies(fields.get("bodies") or ""), tenant=tenant)
    if kind == "escribe":
        value = str(fields.get("escribe_tenant") or fields.get("tenant") or "")
        tenant = _tenant(
            value, _ESCRIBE_HOST, "escribe_tenant", "https://pub-yourcounty.escribemeetings.com"
        )
        return Source(kind, parse_bodies(fields.get("bodies") or ""), tenant=tenant)
    if kind == "civicplus":
        site = _site(str(fields.get("site") or ""))
        return Source(kind, parse_bodies(fields.get("bodies") or ""), site=site)
    if kind == "laserfiche":
        # The desk form names the field apart from PrimeGov's url; the file calls it url.
        value = str(fields.get("laserfiche_url") or fields.get("url") or "")
        url = _laserfiche_url(value)
        return Source(kind, parse_bodies(fields.get("bodies") or ""), url=url)
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


def merge_sources(city_dir: str | Path, found: list[Source]) -> tuple[Path, list[str]]:
    """Add found sources to the file. A source already there gains the bodies it lacked.

    Returns the path and one line per change, so the caller can say what was written.
    """
    sources = load_sources(city_dir)
    changes: list[str] = []
    for new in found:
        at = next((n for n, s in enumerate(sources) if s.id == new.id), None)
        if at is None:
            sources.append(new)
            changes.append(f"added {new.address} for {', '.join(new.bodies)}")
            continue
        old = sources[at]
        known = {b.casefold() for b in old.bodies}
        extra = tuple(b for b in new.bodies if b.casefold() not in known)
        if extra:
            sources[at] = replace(old, bodies=old.bodies + extra)
            changes.append(f"{old.address} now also covers {', '.join(extra)}")
    return save_sources(city_dir, sources), changes


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
