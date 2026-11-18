"""Still images from the meeting video, for the story cards.

A story gets one picture from its meeting video:

- a YouTube meeting gets that video's thumbnail;
- a meeting with its own direct media (CivicClerk MP4/HLS, eScribe, Vimeo) gets one frame,
  taken with ffmpeg at the story's lead cited moment (or a little way into the video).

The build fetches it once (optional, ``--stills``), keeps the file in a cache folder next to the
episode files, and writes it into the site under ``/assets/stills/``. The pages only ever point
at the site's own copy, so a visitor's browser never contacts YouTube or the source for an image.
Nothing here fails a build: a download or a frame that breaks leaves the story to its topic
graphic (see :mod:`civiccast_brief.site.graphics`).
"""

from __future__ import annotations

import hashlib
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STILLS_ASSET_DIR = "/assets/stills"
CACHE_DIR = "stills"
MAX_BYTES = 2_000_000
#: Biggest first. The first one that answers with a JPEG wins.
THUMBNAILS = ("maxresdefault", "hqdefault")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
#: The card is 16:9, so the frame is saved at the size a card shows.
FRAME_WIDTH = 640
FRAME_HEIGHT = 360
#: The small probe frame used to tell a black or blank moment from a picture.
PROBE_SIZE = (64, 36)
#: A probe frame whose channel values span less than this is one flat colour: skip that moment.
BLANK_SPAN = 12
FFMPEG_TIMEOUT_S = 120
FFPROBE_TIMEOUT_S = 30

Fetch = Callable[[str], bytes | None]
"""Takes a YouTube video id and returns JPEG bytes, or None when there is no still."""

MediaFetch = Callable[[str, float | None], bytes | None]
"""Takes a media address and the lead cited moment and returns JPEG bytes, or None."""

#: How one ffmpeg/ffprobe child is run. Tests pass a fake; nothing here raises on a failure.
Runner = Callable[[list[str]], subprocess.CompletedProcess[bytes] | None]


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


def media_key(media_url: str) -> str:
    """A stable file name for a media address, kept apart from YouTube ids."""
    digest = hashlib.sha256(media_url.encode("utf-8")).hexdigest()[:20]
    return f"media-{digest}"


def still_key(video_url: str | None, media_url: str | None) -> str | None:
    """The name a story's still is cached and served under.

    The source's own media wins over ``video_url``: a direct-media meeting's video link is not a
    YouTube id, so its thumbnail cannot be fetched.
    """
    if media_url:
        return media_key(media_url)
    return youtube_id(video_url)


def still_path(key: str) -> str:
    return f"{STILLS_ASSET_DIR}/{key}.jpg"


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


# --- One frame from the meeting's own media -------------------------------------------------


def _run(argv: list[str], runner: Runner) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return runner(argv)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(argv, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def probe_duration(media_url: str, runner: Runner | None = None) -> float | None:
    """The media's length in seconds, or None when ffprobe cannot tell."""
    argv = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        media_url,
    ]
    done = _run(argv, runner or _default_runner)
    if done is None or done.returncode != 0 or not done.stdout:
        return None
    try:
        seconds = float(done.stdout.decode("utf-8", errors="replace").strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def frame_candidates(lead_seconds: float | None, duration_s: float | None) -> list[float]:
    """The moments to try for a frame, best first.

    The story's lead cited moment comes first. With no cited moment the frame comes from a tenth
    of the way in (past the opening slate). Later moments are fallbacks for a black or blank
    frame, and the middle of the meeting is the last thing tried.
    """
    if lead_seconds is not None and lead_seconds >= 0:
        first = float(lead_seconds)
    elif duration_s:
        first = duration_s * 0.10
    else:
        first = 10.0
    picks = [first, first + 45, first + 120, first + 300]
    if duration_s:
        picks += [duration_s * 0.10, duration_s * 0.25, duration_s * 0.50]
    limit = max(0.0, duration_s - 1.0) if duration_s else None
    found: list[float] = []
    for pick in picks:
        seconds = max(0.0, pick)
        if limit is not None:
            seconds = min(seconds, limit)
        seconds = round(seconds, 1)
        if seconds not in found:
            found.append(seconds)
    return found


def looks_blank(raw: bytes) -> bool:
    """True when a raw RGB probe frame is one flat colour: a black, white or solid slate."""
    if len(raw) < 3:
        return True
    pixels = [raw[i : i + 3] for i in range(0, len(raw) - 2, 3)]
    span = max(
        max(pixel[c] for pixel in pixels) - min(pixel[c] for pixel in pixels) for c in (0, 1, 2)
    )
    return span < BLANK_SPAN


def _probe_frame(media_url: str, seconds: float, runner: Runner) -> bytes | None:
    width, height = PROBE_SIZE
    argv = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{seconds:.3f}",
        "-i",
        media_url,
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:{height}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    done = _run(argv, runner)
    if done is None or done.returncode != 0 or not done.stdout:
        return None
    return done.stdout


def _grab_frame(media_url: str, seconds: float, runner: Runner) -> bytes | None:
    argv = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{seconds:.3f}",
        "-i",
        media_url,
        "-frames:v",
        "1",
        "-vf",
        (
            f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={FRAME_WIDTH}:{FRAME_HEIGHT}"
        ),
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    done = _run(argv, runner)
    if done is None or done.returncode != 0 or not done.stdout:
        return None
    return done.stdout if _is_jpeg(done.stdout) else None


def media_still(
    media_url: str, lead_seconds: float | None, *, runner: Runner | None = None
) -> bytes | None:
    """One JPEG frame from the source's media, at the best moment that is not blank. Never raises."""
    run = runner or _default_runner
    try:
        duration = probe_duration(media_url, run)
        for seconds in frame_candidates(lead_seconds, duration):
            probe = _probe_frame(media_url, seconds, run)
            if probe is None or looks_blank(probe):
                continue
            frame = _grab_frame(media_url, seconds, run)
            if frame is not None:
                return frame
    except Exception:  # noqa: BLE001 - a picture must never fail the build
        return None
    return None


# --- Collecting stills for a build ----------------------------------------------------------


def lead_seconds(item: object) -> float | None:
    """The story's lead cited moment: the first timestamp its article cites, else its first claim."""
    article = getattr(item, "article", None)
    if article is not None:
        for section in article.sections:
            for paragraph in section.paragraphs:
                if paragraph.cites:
                    return float(paragraph.cites[0].seconds)
        citations = [*article.votes, *article.quotes]
        if citations:
            return float(citations[0].seconds)
    claims = getattr(item, "claims", ())
    return float(claims[0].seconds) if claims else None


@dataclass(frozen=True)
class StillRequest:
    """One story's picture: a YouTube id, or a media address and its lead cited moment."""

    key: str
    youtube_id: str | None = None
    media_url: str | None = None
    seconds: float | None = None


def collect(
    requests: Iterable[StillRequest],
    cache: Path,
    fetch: Fetch | None = None,
    fetch_media: MediaFetch | None = None,
) -> dict[str, bytes]:
    """Still key -> JPEG bytes, from the cache first, then the fetchers (when given)."""
    found: dict[str, bytes] = {}
    for request in sorted(set(requests), key=lambda item: item.key):
        cached = cache / f"{request.key}.jpg"
        data: bytes | None = None
        if cached.is_file():
            data = cached.read_bytes()
        elif request.youtube_id and fetch is not None:
            data = fetch(request.youtube_id)
        elif request.media_url and fetch_media is not None:
            data = fetch_media(request.media_url, request.seconds)
        if data is not None and _is_jpeg(data):
            if not cached.is_file():
                cache.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
            found[request.key] = data
    return found
