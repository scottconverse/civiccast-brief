"""Normalized transcript: caption segments with start, end, text, speaker and voice.

``speaker`` is a name (from the roll call) or "unknown". ``voice`` is the diarization label
("voice 01", ...) when diarization ran: the same voice, not a name.

A caption line that starts a new speaker turn carries ``>>`` in the caption file. Here the marks are
removed from ``text`` and kept as the ``turn_start`` flag, so the text is clean and the turns can
still be recovered from transcript.json alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

TURN_MARK = ">>"
_HMS_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")
_WS_RE = re.compile(r"\s+")


@dataclass
class Segment:
    start: float
    end: float
    text: str
    turn_start: bool
    speaker: str | None = None
    voice: str | None = None


def normalize_text(raw: str) -> tuple[str, bool]:
    """Return (clean text, whether this caption line starts a speaker turn)."""
    turn_start = TURN_MARK in raw
    return _WS_RE.sub(" ", raw.replace(TURN_MARK, " ")).strip(), turn_start


def normalize_quote(text: str) -> str:
    """A quote as the transcript index stores it: no turn marks, single spaces."""
    return _WS_RE.sub(" ", text.replace(TURN_MARK, " ")).strip()


def segments_from_capture(items: Iterable[dict[str, Any]]) -> list[Segment]:
    """Build segments from TownRecord's parsed caption segments (start_ms, end_ms, text).

    Caption lines whose text is empty after normalization are dropped. Lines are kept in file
    order; the caption file is the source, so nothing is merged or reordered.
    """
    segments: list[Segment] = []
    for item in items:
        text, turn_start = normalize_text(str(item["text"]))
        if not text:
            continue
        start = round(int(item["start_ms"]) / 1000, 2)
        end = round(int(item["end_ms"]) / 1000, 2)
        segments.append(Segment(start=start, end=max(start, end), text=text, turn_start=turn_start))
    return segments


def hms(seconds: float) -> str:
    """Seconds as H:MM:SS, the timestamp form the recap format uses (hours not padded)."""
    whole = int(seconds)
    return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def seconds_of(stamp: str) -> int:
    match = _HMS_RE.match(stamp)
    if not match:
        raise ValueError(f"not an H:MM:SS timestamp: {stamp!r}")
    hours, minutes, seconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def to_json(
    segments: list[Segment], *, video_id: str, title: str, meeting_date: str, source: dict[str, Any]
) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "title": title,
        "meeting_date": meeting_date,
        "source": source,
        "segment_count": len(segments),
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "turn_start": s.turn_start,
                "speaker": s.speaker,
                "voice": s.voice,
            }
            for s in segments
        ],
    }


def from_json(data: dict[str, Any]) -> list[Segment]:
    return [
        Segment(
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
            turn_start=bool(item["turn_start"]),
            speaker=item.get("speaker"),
            voice=item.get("voice"),
        )
        for item in data["segments"]
    ]


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip


def meeting_date_from_title(title: str) -> str | None:
    """Read the date out of a city meeting title, e.g. "City Council Regular Session - 20 October
    2026" or "City Council Regular Session 06/23/26". Returns "October 20, 2026" or None."""
    match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", title)
    if match and match.group(2).lower() in _MONTHS:
        day, month, year = int(match.group(1)), _MONTHS[match.group(2).lower()], int(match.group(3))
        return _long_date(year, month, day)
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", title)
    if match:
        month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000
        return _long_date(year, month, day)
    return None


def _long_date(year: int, month: int, day: int) -> str:
    names = [name for name, number in _MONTHS.items() if number == month]
    return f"{names[0].capitalize()} {day}, {year}"
