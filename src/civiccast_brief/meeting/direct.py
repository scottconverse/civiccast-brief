"""Capture a meeting from a source's own media address and its public caption file.

CivicClerk, eScribe and Vimeo listings name a direct MP4 or HLS address. The source usually
also publishes the captions it made for the meeting (CivicClerk's closed-caption URL, eScribe's
iSiLive ``.vtt`` beside the MP4, a Vimeo text track). That file is the transcript: it is
downloaded into ``raw/`` and parsed with the same WebVTT reader the archives use.

A source that lists media but no caption file (Erie and Superior's CivicClerk MP4s do not) is
transcribed on this machine: the audio is fetched with ffmpeg, and the local speech program
(``python -m civiccast_brief.speech transcribe``) turns it into the same caption items, through
the helpers of :mod:`civiccast_brief.record.stt.textflowkit` that the record pipeline uses. No
hosted model is called and nothing beyond the source's own media is downloaded. A meeting with
neither captions nor media stops the run with that reason.

The media address is also what ``--diarize`` asks for audio, as Granicus and Cablecast do.
"""

from __future__ import annotations

import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from civiccast_brief.record.captions.parse import CaptionParseError, parse_vtt
from civiccast_brief.record.stt.textflowkit import (
    parse_textflowkit_json,
    speech_command,
    textflowkit_argv,
    transcription_timeout_s,
)
from civiccast_brief.speech.windows_process import hidden_process_kwargs

FETCH_TIMEOUT_S = 120
AUDIO_TIMEOUT_S = 3600

#: The reason recorded when a direct-media meeting has no media address at all.
NO_MEDIA = "the source lists no playable media for this meeting"

#: How one child process is run. Tests pass a fake that writes the transcript the speech
#: program would have written, so no test runs a model or reaches the network.
Runner = Callable[[list[str], float], subprocess.CompletedProcess[bytes]]


class DirectError(RuntimeError):
    """A caption file or media address could not be read, with the reason."""


@dataclass
class DirectCapture:
    caption_file: Path
    caption_reused: bool
    items: list[dict[str, Any]]
    #: True when the lines came from this machine's speech program rather than the source.
    transcribed: bool = False


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
    """The meeting's captions, saved in ``raw_dir`` and parsed. Reused when already saved.

    The source's own caption file is read when it lists one. Otherwise the audio of
    ``media_url`` is transcribed on this machine; a meeting with no media address cannot run.
    """
    name = meeting_id or "meeting"
    if not captions_url:
        if not media_url:
            raise DirectError(f"{name} cannot run: {NO_MEDIA}")
        return transcribe(media_url, raw_dir, name)
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
        raise DirectError(f"the caption file for {name} holds no caption lines ({captions_url})")
    return DirectCapture(caption_file=caption_file, caption_reused=reused, items=items)


def _run(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess[bytes]:
    """Run the speech program as a child of this process."""
    return subprocess.run(
        argv, capture_output=True, timeout=timeout_s, check=False, **hidden_process_kwargs()
    )


def _transcript_file(stdout: bytes, out_dir: Path) -> Path | None:
    """The JSON file the speech program wrote: the path it printed, else the newest here."""
    text = stdout.decode("utf-8", errors="replace") if stdout else ""
    for line in text.splitlines():
        candidate = line.strip().strip("'\"")
        if not candidate.endswith(".json"):
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = out_dir / candidate
        if path.is_file():
            return path
    written = [entry for entry in out_dir.glob("*.json") if entry.is_file()]
    return max(written, key=lambda entry: entry.stat().st_mtime) if written else None


def transcribe(
    media_url: str,
    raw_dir: Path,
    meeting_id: str,
    *,
    ffmpeg: str = "ffmpeg",
    speech: list[str] | None = None,
    runner: Runner | None = None,
) -> DirectCapture:
    """Transcribe the meeting's audio on this machine, as caption items.

    The audio comes from ``fetch_audio`` (16 kHz mono WAV; the video is not downloaded), and
    the speech program is the one the record pipeline runs. Its JSON is kept in
    ``raw/transcript/`` and reused on a later run. A failed or empty transcription stops the
    run with its reason.
    """
    out_dir = raw_dir / "transcript"
    name = meeting_id or "meeting"
    reused: list[Path] = (
        sorted(out_dir.glob("*.json"), key=lambda entry: entry.stat().st_mtime)
        if out_dir.is_dir()
        else []
    )
    if reused:
        transcript_file = reused[-1]
    else:
        audio = fetch_audio(media_url, raw_dir / "audio", name, ffmpeg=ffmpeg)
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = [*(speech or speech_command()), *textflowkit_argv(str(audio), str(out_dir))[1:]]
        run = runner or _run
        try:
            done = run(argv, transcription_timeout_s(None))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DirectError(f"could not run the speech program for {name}: {exc}") from exc
        if done.returncode != 0:
            tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
            raise DirectError(f"transcription failed for {name}: {tail}")
        found = _transcript_file(done.stdout, out_dir)
        if found is None:
            raise DirectError(f"the speech program wrote no transcript for {name}")
        transcript_file = found
    try:
        segments = parse_textflowkit_json(transcript_file.read_bytes())
    except (CaptionParseError, OSError) as exc:
        raise DirectError(f"the transcript for {name} could not be read: {exc}") from exc
    items = [
        {"start_ms": segment.start_ms, "end_ms": segment.end_ms, "text": segment.text}
        for segment in segments
    ]
    if not items:
        raise DirectError(f"the speech program transcribed {name} to no lines")
    return DirectCapture(
        caption_file=transcript_file,
        caption_reused=bool(reused),
        items=items,
        transcribed=True,
    )


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
        done = subprocess.run(
            argv,
            capture_output=True,
            timeout=AUDIO_TIMEOUT_S,
            check=False,
            **hidden_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DirectError(f"could not run ffmpeg for the audio: {exc}") from exc
    if done.returncode != 0 or not part.exists():
        tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        raise DirectError(f"audio fetch failed for {meeting_id}: {tail}")
    part.replace(wav)
    return wav
