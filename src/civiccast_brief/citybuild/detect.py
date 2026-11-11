"""Which meeting platform an address belongs to, by its shape alone.

Each rule is a host or path pattern that the platform uses for every city, checked against the
addresses recorded for Boulder County's governments (``tests/citybuild``). A match says only which
platform it is; :mod:`civiccast_brief.citybuild.search` then asks the platform itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs, unquote, urlsplit

#: Platforms this program can also read meetings from. The rest are found and reported only.
READABLE: Final = (
    "primegov",
    "youtube",
    "granicus",
    "webpage",
    "gdrive",
    "civicclerk",
    "escribe",
    "civicplus",
)

LABELS: Final = {
    "primegov": "PrimeGov",
    "legistar": "Legistar",
    "granicus": "Granicus",
    "civicclerk": "CivicClerk",
    "escribe": "eScribe",
    "boarddocs": "BoardDocs",
    "civicplus": "CivicPlus Agenda Center",
    "civicweb": "CivicWeb",
    "laserfiche": "Laserfiche WebLink",
    "onboard": "OnBoard",
    "youtube": "YouTube",
    "vimeo": "Vimeo",
    "gdrive": "Google Drive folder",
    "webpage": "Web page with meeting documents",
}

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTS: Final = (
    ("primegov", re.compile(rf"^({_LABEL})\.primegov\.com$")),
    ("legistar", re.compile(rf"^({_LABEL})\.legistar\.com$")),
    ("granicus", re.compile(rf"^({_LABEL})\.granicus\.com$")),
    ("civicclerk", re.compile(rf"^({_LABEL})\.portal\.civicclerk\.com$")),
    ("escribe", re.compile(rf"^pub-({_LABEL})\.escribemeetings\.com$")),
    ("civicweb", re.compile(rf"^({_LABEL})\.civicweb\.net$")),
)
_BOARDDOCS: Final = re.compile(r"^/([a-z]{2})/([a-z0-9_-]+)/board\.nsf", re.IGNORECASE)
_CHANNEL: Final = re.compile(r"^/channel/(UC[A-Za-z0-9_-]{22})(?:/|$)")
_HANDLE: Final = re.compile(r"^/(@[A-Za-z0-9._-]{3,100}|user/[A-Za-z0-9_-]+|c/[^/]+)(?:/|$)")
# youtube.com/cityoflongmont: a bare custom name. Paths YouTube uses for itself are not names.
_BARE: Final = re.compile(r"^/([A-Za-z0-9_-]{3,100})/?$")
_YOUTUBE_OWN: Final = frozenset(
    {
        "watch",
        "playlist",
        "results",
        "feed",
        "embed",
        "shorts",
        "live",
        "about",
        "redirect",
        "account",
        "premium",
        "kids",
        "music",
        "gaming",
        "channel",
        "user",
        "c",
        "t",
        "s",
        "hashtag",
        "howyoutubeworks",
        "creators",
        "ads",
        "yt",
        "signin",
        "logout",
        "upload",
    }
)
_DRIVE_FOLDER: Final = re.compile(r"/(?:drive/(?:u/\d+/)?folders/|embeddedfolderview)")
_FOLDER_ID: Final = re.compile(r"^[A-Za-z0-9_-]{10,100}$")


@dataclass(frozen=True)
class Found:
    """One platform address. ``key`` is what the platform calls this city (its tenant)."""

    platform: str
    url: str
    key: str = ""
    #: Granicus view id, or the YouTube handle still to be turned into a channel id.
    extra: str = ""

    @property
    def label(self) -> str:
        return LABELS.get(self.platform, self.platform)


def classify(url: str) -> Found | None:
    """The platform an address belongs to, or None for an ordinary web page."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    if host.startswith("www."):
        bare_host = host[4:]
    else:
        bare_host = host
    for platform, pattern in _HOSTS:
        match = pattern.match(bare_host)
        if match:
            key = match.group(1)
            if platform == "legistar" and key == "webapi":
                parts_path = path.strip("/").split("/")
                key = parts_path[1] if len(parts_path) > 1 else ""
            extra = ""
            if platform == "granicus":
                extra = (parse_qs(parts.query).get("view_id") or [""])[0]
                if not extra.isdigit():
                    extra = ""
            return Found(platform, url.strip(), key, extra)
    if bare_host == "go.boarddocs.com":
        match = _BOARDDOCS.match(path)
        if match:
            return Found("boarddocs", url.strip(), f"{match[1].lower()}/{match[2].lower()}")
    if path.lower().startswith("/agendacenter") or path.lower() == "/archive.aspx":
        return Found("civicplus", url.strip(), bare_host)
    if path.lower().startswith("/weblink/"):
        return Found("laserfiche", url.strip(), bare_host)
    if bare_host == "public.onboardmeetings.com":
        return Found("onboard", url.strip())
    if bare_host in ("youtube.com", "m.youtube.com"):
        return _youtube(url.strip(), path, parts.query)
    if bare_host == "youtu.be":
        return None
    if bare_host == "vimeo.com":
        name = path.strip("/").split("/")[0]
        return Found("vimeo", url.strip(), name) if name else None
    if bare_host == "drive.google.com" and _DRIVE_FOLDER.search(path):
        folder = drive_folder_id(url)
        return Found("gdrive", url.strip(), folder) if folder else None
    return None


def _youtube(url: str, path: str, query: str) -> Found | None:
    channel = _CHANNEL.match(path)
    if channel:
        return Found("youtube", url, channel[1])
    handle = _HANDLE.match(path)
    if handle:
        return Found("youtube", url, "", unquote(handle[1]))
    if path.startswith("/playlist"):
        playlist = (parse_qs(query).get("list") or [""])[0]
        return Found("youtube", url, "", f"playlist:{playlist}") if playlist else None
    bare = _BARE.match(path)
    if bare and bare[1].lower() not in _YOUTUBE_OWN:
        return Found("youtube", url, "", bare[1])
    return None


def drive_folder_id(url: str) -> str:
    parts = urlsplit(url)
    if "embeddedfolderview" in parts.path:
        value = (parse_qs(parts.query).get("id") or [""])[0]
    else:
        value = parts.path.rstrip("/").rsplit("/", 1)[-1]
    value = value.split("\\")[0]
    return value if _FOLDER_ID.match(value) else ""
