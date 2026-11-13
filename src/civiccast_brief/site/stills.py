"""Still images from the meeting video, for the front page.

A story with a YouTube meeting video gets that video's thumbnail. The build downloads it once
(optional, ``--stills``), keeps the file in a cache folder next to the episode files, and writes
it into the site under ``/assets/stills/``. The pages only ever point at the site's own copy, so a
visitor's browser never contacts YouTube for an image. A video with no still gets a plain colored
block. Nothing here fails a build: a download that breaks leaves the block.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STILLS_ASSET_DIR = "/assets/stills"
CACHE_DIR = "stills"
MAX_BYTES = 2_000_000
#: Biggest first. The first one that answers with a JPEG wins.
THUMBNAILS = ("maxresdefault", "hqdefault")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

Fetch = Callable[[str], bytes | None]
"""Takes a YouTube video id and returns JPEG bytes, or None when there is no still."""


def youtube_id(url: str | None) -> str | None:
    """The video id of a YouTube watch or youtu.be link, else None."""
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in _HOSTS:
        return None
    if host == "youtu.be":
        found = parsed.path.strip("/")
    else:
        found = parse_qs(parsed.query).get("v", [""])[0]
    return found if found and found.replace("-", "").replace("_", "").isalnum() else None


def still_path(video_id: str) -> str:
    return f"{STILLS_ASSET_DIR}/{video_id}.jpg"


def _is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" and 0 < len(data) <= MAX_BYTES


def download(video_id: str) -> bytes | None:
    """The thumbnail from YouTube's image host, or None. Never raises."""
    for name in THUMBNAILS:
        url = f"https://i.ytimg.com/vi/{video_id}/{name}.jpg"
        request = urllib.request.Request(url, headers={"User-Agent": "civiccast-brief-build"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data: bytes = response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if _is_jpeg(data):
            return data
    return None


def collect(video_ids: Iterable[str], cache: Path, fetch: Fetch | None) -> dict[str, bytes]:
    """Video id -> JPEG bytes, from the cache first, then ``fetch`` (when given)."""
    found: dict[str, bytes] = {}
    for video_id in sorted(set(video_ids)):
        cached = cache / f"{video_id}.jpg"
        data: bytes | None = None
        if cached.is_file():
            data = cached.read_bytes()
        elif fetch is not None:
            data = fetch(video_id)
            if data is not None and _is_jpeg(data):
                cache.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
        if data is not None and _is_jpeg(data):
            found[video_id] = data
    return found
