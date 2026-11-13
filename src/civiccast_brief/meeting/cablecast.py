"""Cablecast capture: a city's meeting list, a show's caption file and its audio.

A city whose council video is on Cablecast (Tightrope "Reflect") has a public site at
``https://<name>.cablecast.tv`` and a public JSON API at ``/cablecastapi/v1``. ``shows`` lists the
newest shows with their titles, event dates and video-on-demand (vod) ids; a show with no vod has
no video and is skipped. ``vods/<id>`` names the vod's MP4 file, which ffmpeg reads for the audio.

The MP4 carries no caption stream. The captions are WebVTT segments (about ten seconds each) named
by ``vod.m3u8`` (a ``SUBTITLES`` entry) and then by that playlist, in the MP4's folder. Their cue
times are absolute from the start of the video. The segments are joined here, and a cue repeated
at the edge of two segments is kept once.

A show whose vod has no caption playlist, or no caption lines, stops the run with that reason;
nothing is transcribed from audio here.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from civiccast_brief.meeting.granicus import parse_vtt
from civiccast_brief.record.video.watch import title_day

FETCH_TIMEOUT_S = 60
AUDIO_TIMEOUT_S = 3600
#: How many of the newest shows one listing asks for. A week's meetings are far inside it.
LIST_SIZE = 200
SEGMENT_WORKERS = 8
SEGMENT_TRIES = 3

_SUBTITLES_RE = re.compile(r"^#EXT-X-MEDIA:.*TYPE=SUBTITLES.*$", re.MULTILINE)
_URI_RE = re.compile(r'URI="([^"]+)"')


class CablecastError(RuntimeError):
    """A Cablecast page or file could not be read, with the reason."""


@dataclass(frozen=True)
class Show:
    host: str
    show_id: int
    vod_id: int
    name: str
    date: str  # "November 4, 2026"
    #: The vod's MP4 file; empty until the vod is read (the list does not name it).
    mp4_url: str = ""
    length_s: int = 0


def _fetch(url: str) -> str:
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            return response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise CablecastError(f"could not fetch {url}: {exc}") from exc


def _api(host: str) -> str:
    return f"https://{host}/cablecastapi/v1"


def _json(url: str) -> dict[str, Any]:
    try:
        data = json.loads(_fetch(url))
    except ValueError as exc:
        raise CablecastError(f"{url} did not answer with JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CablecastError(f"{url} did not answer with a JSON object")
    return data


def _long(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def _show(host: str, row: dict[str, Any]) -> Show | None:
    """One show row, or None when it has no vod or no readable day."""
    vods = row.get("vods") or []
    if not vods:
        return None
    title = " ".join(str(row.get("title") or "").split())
    # eventDate is local midnight with its offset ("2026-11-04T00:00:00-07:00"): its first ten
    # characters are the city's own day. A title that names one day wins; a show is sometimes
    # dated the day after its meeting.
    try:
        event_day = date.fromisoformat(str(row.get("eventDate") or "")[:10])
    except ValueError:
        event_day = None
    day = title_day(title) or event_day
    if day is None or not title:
        return None
    return Show(host, int(row["id"]), int(vods[0]), title, _long(day))


def parse_shows(
    data: dict[str, Any], host: str, choose: Callable[[list[Show]], Show] | None = None
) -> list[Show]:
    """The shows that have a video, in the order given (newest first). Cablecast keeps a show per
    airing, so two shows with one title and day are one meeting: ``choose`` picks one of them
    (default the first listed). A group keeps the place of its first show."""
    groups: dict[tuple[str, str], list[Show]] = {}
    for row in data.get("shows") or []:
        show = _show(host, row)
        if show is not None:
            groups.setdefault((show.name.casefold(), show.date), []).append(show)
    pick = choose or (lambda group: group[0])
    return [group[0] if len(group) == 1 else pick(group) for group in groups.values()]


def has_captions(show: Show) -> bool:
    """Whether the show's vod names a caption playlist. The list's own ``hasCaptions`` flag is
    not used: Loveland's two copies of one meeting have it the wrong way round."""
    try:
        full = find_show(show.host, show.show_id)
        base = full.mp4_url.rsplit("/", 1)[0] + "/"
        return subtitles_uri(_fetch(base + "vod.m3u8")) is not None
    except CablecastError:
        return False


def _captioned(group: list[Show]) -> Show:
    """The first copy of a meeting whose vod has captions, else the first copy."""
    return next((show for show in group if has_captions(show)), group[0])


def list_shows(host: str) -> list[Show]:
    """The newest shows of every body, not only the council: the caller picks the bodies.
    Captions are looked for only to choose between copies of one meeting."""
    return parse_shows(_json(f"{_api(host)}/shows?pageSize={LIST_SIZE}"), host, _captioned)


def find_show(host: str, show_id: int) -> Show:
    """One show with its vod read: the MP4 address and the length."""
    data = _json(f"{_api(host)}/shows/{show_id}")
    row = data.get("show")
    show = _show(host, row) if isinstance(row, dict) else None
    if show is None:
        raise CablecastError(f"show {show_id} on https://{host} has no video")
    vod = _json(f"{_api(host)}/vods/{show.vod_id}").get("vod")
    url = str(vod.get("url") or "") if isinstance(vod, dict) else ""
    if not url.startswith("https://"):
        raise CablecastError(f"show {show_id} on {host}: its vod names no MP4 file")
    length = int(vod.get("length") or 0)
    return Show(host, show.show_id, show.vod_id, show.name, show.date, url, length)


def subtitles_uri(master: str) -> str | None:
    """The caption playlist a ``vod.m3u8`` names (its ``SUBTITLES`` entry), or None."""
    line = _SUBTITLES_RE.search(master)
    match = _URI_RE.search(line.group(0)) if line else None
    return match.group(1) if match else None


def segment_uris(playlist: str) -> list[str]:
    """The caption segments a playlist lists, in order."""
    return [line.strip() for line in playlist.splitlines() if line.strip() and line[0] != "#"]


def join_segments(vtts: list[str]) -> list[dict[str, Any]]:
    """Caption items from the segments, in order. A cue that repeats at the edge of two
    segments (the same start, end and words) is kept once."""
    items: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for vtt in vtts:
        for item in parse_vtt(vtt):
            key = (item["start_ms"], item["end_ms"], item["text"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    items.sort(key=lambda item: item["start_ms"])
    return items


def _stamp(ms: int) -> str:
    seconds, milli = divmod(ms, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d}.{milli:03d}"


def to_vtt(items: list[dict[str, Any]]) -> str:
    cues = [f"{_stamp(i['start_ms'])} --> {_stamp(i['end_ms'])}\n{i['text']}\n" for i in items]
    return "WEBVTT\n\n" + "\n".join(cues)


def _segment(url: str) -> str:
    last: CablecastError | None = None
    for _ in range(SEGMENT_TRIES):
        try:
            return _fetch(url)
        except CablecastError as exc:
            last = exc
    raise last or CablecastError(f"could not fetch {url}")


def _same_site(base: str, url: str) -> str:
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != urlsplit(base).hostname:
        raise CablecastError(f"{url} is not on {urlsplit(base).hostname}")
    return url


def fetch_captions(show: Show) -> list[dict[str, Any]]:
    """The show's caption items, read from the segments next to its MP4 file."""
    base = show.mp4_url.rsplit("/", 1)[0] + "/"
    no_captions = (
        f"show {show.show_id} on {show.host} has no caption lines; this city does not keep "
        "captions for it, and CivicCast Brief does not transcribe audio"
    )
    try:
        master = _fetch(base + "vod.m3u8")
    except CablecastError as exc:
        raise CablecastError(f"{no_captions} (no vod.m3u8 playlist: {exc})") from exc
    uri = subtitles_uri(master)
    if not uri:
        raise CablecastError(no_captions)
    playlist_url = _same_site(base, urljoin(base, uri))
    playlist = _fetch(playlist_url)
    urls = [_same_site(base, urljoin(playlist_url, u)) for u in segment_uris(playlist)]
    with ThreadPoolExecutor(max_workers=SEGMENT_WORKERS) as pool:
        vtts = list(pool.map(_segment, urls))
    items = join_segments(vtts)
    if not items:
        raise CablecastError(no_captions)
    return items


@dataclass
class CablecastCapture:
    show: Show
    caption_file: Path
    caption_reused: bool
    items: list[dict[str, Any]]

    @property
    def meeting_id(self) -> str:
        return meeting_id(self.show.host, self.show.show_id)

    @property
    def video_url(self) -> str:
        return video_url(self.show.host, self.show.show_id)


def meeting_id(host: str, show_id: int) -> str:
    return f"cablecast-{host.split('.')[0]}-{show_id}"


def video_url(host: str, show_id: int) -> str:
    """The show's public watch page."""
    return f"https://{host}/internetchannel/show/{show_id}"


def capture(host: str, show_id: int, raw_dir: Path) -> CablecastCapture:
    """The show's row and its joined caption file, saved in ``raw_dir``. Reused when saved."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    show_file = raw_dir / "show.json"
    vtt_file = raw_dir / f"{show_id}.captions.vtt"
    reused = vtt_file.exists() and show_file.exists()
    if reused:
        show = Show(**json.loads(show_file.read_text(encoding="utf-8")))
    else:
        show = find_show(host, show_id)
        items = fetch_captions(show)
        vtt_file.write_text(to_vtt(items), encoding="utf-8")
        show_file.write_text(json.dumps(asdict(show), indent=2) + "\n", encoding="utf-8")
    items = parse_vtt(vtt_file.read_text(encoding="utf-8"))
    if not items:
        raise CablecastError(
            f"show {show_id} on {host} has no caption lines; this city does not keep captions "
            "for it, and CivicCast Brief does not transcribe audio"
        )
    return CablecastCapture(show=show, caption_file=vtt_file, caption_reused=reused, items=items)


def fetch_audio(cap: CablecastCapture, work_dir: Path, *, ffmpeg: str = "ffmpeg") -> Path:
    """The show audio as 16 kHz mono WAV, ``<work_dir>/<meeting id>.wav``. Reused when present."""
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / f"{cap.meeting_id}.wav"
    if wav.exists() and wav.stat().st_size > 0:
        return wav
    part = wav.with_suffix(".part.wav")
    argv = [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", cap.show.mp4_url, "-vn"]
    argv += ["-ac", "1", "-ar", "16000", str(part)]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=AUDIO_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CablecastError(f"could not run ffmpeg for the audio: {exc}") from exc
    if done.returncode != 0 or not part.exists():
        tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        raise CablecastError(f"audio fetch failed for {cap.meeting_id}: {tail}")
    part.replace(wav)
    return wav
