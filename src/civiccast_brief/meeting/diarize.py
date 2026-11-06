"""Voice diarization: who spoke when, as voice labels on the caption lines.

1. The meeting audio is fetched once with yt-dlp in TownRecord's venv, as 16 kHz mono WAV.
2. textflowkit's diarizer runs in textflowkit's own venv (textflowkit_bridge.py), from the local
   model cache, offline. It writes diarization.json: a list of (start, end, label) spans.
3. Each caption line gets the voice that overlaps it most. Labels are renumbered "voice 01",
   "voice 02", ... in order of first appearance.

A voice label is not a name: it says two lines are the same voice, never who that is. Names still
come only from the roll call (speakers.py).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from civiccast_brief.meeting.capture import townrecord_python
from civiccast_brief.meeting.transcript import Segment

BRIDGE = Path(__file__).with_name("textflowkit_bridge.py")
TEXTFLOWKIT_PYTHON_ENV = "CIVICCAST_TEXTFLOWKIT_PYTHON"
DEFAULT_TEXTFLOWKIT_PYTHON = r"C:\Users\scott\Desktop\Code\textflowkit\.venv\Scripts\python.exe"
AUDIO_TIMEOUT_S = 1800
DIARIZE_TIMEOUT_S = 7200


class DiarizeError(RuntimeError):
    """Audio fetch or diarization failed, with the reason."""


@dataclass(frozen=True)
class Span:
    start: float
    end: float
    label: str


def textflowkit_python(explicit: str | None = None) -> str:
    return explicit or os.environ.get(TEXTFLOWKIT_PYTHON_ENV) or DEFAULT_TEXTFLOWKIT_PYTHON


def fetch_audio(video_id: str, work_dir: Path, *, python: str | None = None) -> Path:
    """The meeting audio as ``<work_dir>/<video_id>.wav``. Reused when it is already there."""
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / f"{video_id}.wav"
    if wav.exists() and wav.stat().st_size > 0:
        return wav
    argv = [
        townrecord_python(python),
        "-I",
        "-m",
        "yt_dlp",
        "-q",
        "--no-progress",
        "-f",
        "bestaudio",
        "-x",
        "--audio-format",
        "wav",
        "--postprocessor-args",
        "ffmpeg:-ac 1 -ar 16000",
        "-o",
        str(work_dir / "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=AUDIO_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiarizeError(f"could not run yt-dlp for the audio: {exc}") from exc
    if done.returncode != 0 or not wav.exists():
        tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        raise DiarizeError(f"audio fetch failed for {video_id}: {tail}")
    return wav


def run_diarization(audio: Path, out_json: Path, *, python: str | None = None) -> dict[str, Any]:
    """Run textflowkit's diarizer on ``audio`` into ``out_json``. Reused when it exists."""
    if not out_json.exists():
        argv = [textflowkit_python(python), "-I", str(BRIDGE), str(audio), str(out_json)]
        try:
            done = subprocess.run(argv, capture_output=True, timeout=DIARIZE_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiarizeError(f"could not run textflowkit's diarizer: {exc}") from exc
        lines = done.stdout.decode("utf-8", errors="replace").strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {"error": "no output from the bridge"}
        if done.returncode != 0 or "error" in payload or not out_json.exists():
            raise DiarizeError(f"diarization failed: {payload.get('error', payload)}")
    return dict(json.loads(out_json.read_text(encoding="utf-8")))


def spans_from(data: dict[str, Any]) -> list[Span]:
    return [Span(float(t["start"]), float(t["end"]), str(t["speaker"])) for t in data["turns"]]


def assign_voices(segments: list[Segment], spans: list[Span]) -> dict[str, Any]:
    """Give each caption line the voice that overlaps it most; lines no voice overlaps get none.

    Spans are sorted by start, so one forward pass is enough. Returns counts for the report.
    """
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    names: dict[str, str] = {}
    first = 0
    labelled = 0
    for segment in segments:
        while first < len(ordered) and ordered[first].end <= segment.start:
            first += 1
        best: Span | None = None
        best_overlap = 0.0
        for span in ordered[first:]:
            if span.start >= segment.end:
                break
            amount = min(segment.end, span.end) - max(segment.start, span.start)
            if amount > best_overlap:
                best, best_overlap = span, amount
        if best is None:
            segment.voice = None
            continue
        if best.label not in names:
            names[best.label] = f"voice {len(names) + 1:02d}"
        segment.voice = names[best.label]
        labelled += 1
    return {
        "voices": len(names),
        "segments_with_voice": labelled,
        "segments_without_voice": len(segments) - labelled,
        "labels": names,
    }


def not_run(reason: str) -> dict[str, Any]:
    return {"status": "not run", "reason": reason}
