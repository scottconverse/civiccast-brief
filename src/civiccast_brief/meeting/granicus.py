"""Granicus capture: a city's meeting list, a clip's caption file and its audio.

A Granicus city publishes its meetings at ``https://<host>/ViewPublisher.php?view_id=<n>``.
Each row has the meeting name, its date and a ``MediaPlayer.php?...clip_id=<id>`` link.
The clip's captions, when the city keeps them, are at ``https://<host>/videos/<id>/captions.vtt``.
The player page names the clip's MP4 file, which ffmpeg reads for the audio.

Not every Granicus city keeps captions (some caption files are empty). A clip with no caption
lines stops the run with that reason; nothing is transcribed from audio here.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

USER_AGENT = "CivicCastBrief/0.1 (+https://civiccastbrief.org)"
FETCH_TIMEOUT_S = 60
AUDIO_TIMEOUT_S = 3600

_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<td\b([^>]*)>(.*?)(?=<td\b|</tr>)", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_CLIP_RE = re.compile(r"MediaPlayer\.php\?view_id=(\d+)&(?:amp;)?clip_id=(\d+)", re.IGNORECASE)
_MP3_RE = re.compile(r"https?://[^\"'\s]+\.mp3", re.IGNORECASE)
_MP4_RE = re.compile(r"https://archive-video\.granicus\.com/[^\"'\s]+\.mp4", re.IGNORECASE)
_CUE_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})\.(\d{3})")
_DATE_RE = re.compile(r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s*(\d{4})")


class GranicusError(RuntimeError):
    """A Granicus page or file could not be read, with the reason."""


@dataclass(frozen=True)
class Clip:
    host: str
    view_id: int
    clip_id: int
    name: str
    date: str  # "October 6, 2026"
    mp3_url: str | None = None


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            return response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise GranicusError(f"could not fetch {url}: {exc}") from exc


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", fragment)).replace("\xa0", " ").split())


def long_date(text: str) -> str | None:
    """ "Oct  8, 2026" or "October 6, 2026" as "October 8, 2026"."""
    match = _DATE_RE.search(text)
    if not match:
        return None
    month, day, year = match.groups()
    for form in ("%B", "%b"):
        try:
            parsed = datetime.strptime(f"{month} {day} {year}", f"{form} %d %Y")
        except ValueError:
            continue
        return f"{parsed:%B} {parsed.day}, {parsed.year}"
    return None


def parse_publisher(page: str, host: str) -> list[Clip]:
    """Every archived clip on a ViewPublisher page, in page order. Rows without a video link
    (upcoming meetings) are skipped."""
    clips: list[Clip] = []
    seen: set[int] = set()
    for row in _ROW_RE.findall(page):
        link = _CLIP_RE.search(row)
        if not link:
            continue
        name = date = None
        for attrs, body in _CELL_RE.findall(row):
            headers = re.search(r'headers="([^"]*)"', attrs)
            first = headers.group(1).split()[0] if headers else ""
            if first == "Name" and name is None:
                name = _text(body)
            elif first == "Date" and date is None:
                date = long_date(_text(body))
        clip_id = int(link.group(2))
        if not name or not date or clip_id in seen:
            continue
        seen.add(clip_id)
        mp3 = _MP3_RE.search(row)
        clips.append(
            Clip(
                host=host,
                view_id=int(link.group(1)),
                clip_id=clip_id,
                name=name.lstrip("* ").strip(),
                date=date,
                mp3_url=mp3.group(0) if mp3 else None,
            )
        )
    return clips


def list_clips(host: str, view_id: int) -> list[Clip]:
    return parse_publisher(_fetch(f"https://{host}/ViewPublisher.php?view_id={view_id}"), host)


def find_clip(host: str, view_id: int, clip_id: int) -> Clip:
    for clip in list_clips(host, view_id):
        if clip.clip_id == clip_id:
            return clip
    raise GranicusError(
        f"clip {clip_id} is not on https://{host}/ViewPublisher.php?view_id={view_id}"
    )


def _ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def parse_vtt(text: str) -> list[dict[str, Any]]:
    """WebVTT cues as caption items: ``{"start_ms", "end_ms", "text"}``. Cue settings, ids and
    tags are dropped; an end before its start is kept as given (the transcript clamps it)."""
    items: list[dict[str, Any]] = []
    lines = text.replace("\r\n", "\n").split("\n")
    position = 0
    while position < len(lines):
        match = _CUE_RE.match(lines[position].strip())
        position += 1
        if not match:
            continue
        words: list[str] = []
        while position < len(lines) and lines[position].strip():
            words.append(_text(lines[position]))
            position += 1
        body = " ".join(w for w in words if w)
        if body:
            items.append(
                {
                    "start_ms": _ms(*match.groups()[:4]),
                    "end_ms": _ms(*match.groups()[4:]),
                    "text": body,
                }
            )
    return items


@dataclass
class GranicusCapture:
    clip: Clip
    caption_file: Path
    caption_reused: bool
    items: list[dict[str, Any]]

    @property
    def meeting_id(self) -> str:
        return meeting_id(self.clip.host, self.clip.clip_id)

    @property
    def video_url(self) -> str:
        return video_url(self.clip.host, self.clip.view_id, self.clip.clip_id)


def meeting_id(host: str, clip_id: int) -> str:
    return f"granicus-{host.split('.')[0]}-{clip_id}"


def video_url(host: str, view_id: int, clip_id: int) -> str:
    return f"https://{host}/MediaPlayer.php?view_id={view_id}&clip_id={clip_id}"


def capture(host: str, view_id: int, clip_id: int, raw_dir: Path) -> GranicusCapture:
    """The clip's row and caption file, saved in ``raw_dir``. Reused when already saved."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    clip_file = raw_dir / "clip.json"
    vtt_file = raw_dir / f"{clip_id}.captions.vtt"
    reused = vtt_file.exists() and clip_file.exists()
    if reused:
        clip = Clip(**json.loads(clip_file.read_text(encoding="utf-8")))
    else:
        clip = find_clip(host, view_id, clip_id)
        vtt_file.write_text(
            _fetch(f"https://{host}/videos/{clip_id}/captions.vtt"), encoding="utf-8"
        )
        clip_file.write_text(json.dumps(asdict(clip), indent=2) + "\n", encoding="utf-8")
    items = parse_vtt(vtt_file.read_text(encoding="utf-8"))
    if not items:
        raise GranicusError(
            f"clip {clip_id} on {host} has no caption lines; this city does not keep captions "
            "for it, and CivicCast Brief does not transcribe audio"
        )
    return GranicusCapture(clip=clip, caption_file=vtt_file, caption_reused=reused, items=items)


def media_url(host: str, view_id: int, clip_id: int, mp3_url: str | None = None) -> str:
    """The clip's audio source: the MP3 link from the meeting list, else the player's MP4."""
    if mp3_url:
        return mp3_url
    page = _fetch(video_url(host, view_id, clip_id))
    match = _MP4_RE.search(page)
    if not match:
        raise GranicusError(f"no MP4 file named on the player page for clip {clip_id}")
    return match.group(0)


def fetch_audio(cap: GranicusCapture, work_dir: Path, *, ffmpeg: str = "ffmpeg") -> Path:
    """The clip audio as 16 kHz mono WAV, ``<work_dir>/<meeting id>.wav``. Reused when present."""
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / f"{cap.meeting_id}.wav"
    if wav.exists() and wav.stat().st_size > 0:
        return wav
    source = media_url(cap.clip.host, cap.clip.view_id, cap.clip.clip_id, cap.clip.mp3_url)
    part = wav.with_suffix(".part.wav")
    argv = [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", source, "-vn", "-ac", "1"]
    argv += ["-ar", "16000", str(part)]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=AUDIO_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GranicusError(f"could not run ffmpeg for the audio: {exc}") from exc
    if done.returncode != 0 or not part.exists():
        tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        raise GranicusError(f"audio fetch failed for {cap.meeting_id}: {tail}")
    part.replace(wav)
    return wav
