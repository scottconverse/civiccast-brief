"""Capture a meeting from a source's own media address and its public caption file.

CivicClerk, eScribe and Vimeo listings name a direct MP4 or HLS address. The source usually
also publishes the captions it made for the meeting (CivicClerk's closed-caption URL, eScribe's
iSiLive ``.vtt`` beside the MP4, a Vimeo text track). That file is the transcript: it is
downloaded into ``raw/`` and parsed with the same WebVTT reader the archives use. A source that
lists no caption file stops the run with that reason; nothing is transcribed from audio here.

The media address is used only for the audio ``--diarize`` asks for, as Granicus and Cablecast
do.
"""

from __future__ import annotations

import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from civiccast_brief.record.captions.parse import CaptionParseError, parse_vtt

FETCH_TIMEOUT_S = 120
AUDIO_TIMEOUT_S = 3600


class DirectError(RuntimeError):
    """A caption file or media address could not be read, with the reason."""


@dataclass
class DirectCapture:
    caption_file: Path
    caption_reused: bool
    items: list[dict[str, Any]]


def _fetch(url: str) -> str:
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            return response.read().decode("utf-8-sig", errors="replace")
    except OSError as exc:
        raise DirectError(f"could not fetch {url}: {exc}") from exc


def capture(
    media_url: str,
    captions_url: str | None,
    raw_dir: Path,
    *,
    meeting_id: str = "",
) -> DirectCapture:
    """The meeting's caption file, saved in ``raw_dir`` and parsed. Reused when already saved."""
    name = meeting_id or "meeting"
    if not captions_url:
        raise DirectError(
            f"{name} has no public caption file; this source keeps no captions for it, and "
            "CivicCast Brief does not transcribe audio"
        )
    raw_dir.mkdir(parents=True, exist_ok=True)
    caption_file = raw_dir / "captions.vtt"
    reused = caption_file.exists() and caption_file.stat().st_size > 0
    if not reused:
        text = _fetch(captions_url)
        if not text.strip():
            raise DirectError(f"the caption file for {name} is empty ({captions_url})")
        caption_file.write_text(text, encoding="utf-8", newline="\n")
    try:
        items = [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
            for s in parse_vtt(caption_file.read_bytes())
        ]
    except CaptionParseError as exc:
        raise DirectError(f"the caption file for {name} could not be read: {exc}") from exc
    if not items:
        raise DirectError(
            f"{name} has no caption lines; this source keeps no captions for it, and "
            "CivicCast Brief does not transcribe audio"
        )
    return DirectCapture(caption_file=caption_file, caption_reused=reused, items=items)


def fetch_audio(media_url: str, work_dir: Path, meeting_id: str, *, ffmpeg: str = "ffmpeg") -> Path:
    """The meeting audio as 16 kHz mono WAV, ``<work_dir>/<meeting id>.wav``. Reused when present."""
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / f"{meeting_id}.wav"
    if wav.exists() and wav.stat().st_size > 0:
        return wav
    part = wav.with_suffix(".part.wav")
    argv = [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", media_url, "-vn", "-ac", "1"]
    argv += ["-ar", "16000", str(part)]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=AUDIO_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DirectError(f"could not run ffmpeg for the audio: {exc}") from exc
    if done.returncode != 0 or not part.exists():
        tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        raise DirectError(f"audio fetch failed for {meeting_id}: {tail}")
    part.replace(wav)
    return wav
