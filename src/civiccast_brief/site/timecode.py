"""Timestamps: parse meeting and audio times, and format them for pages, VTT and feeds."""

from __future__ import annotations

import re

_TIMECODE_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(\.\d{1,3})?$")
_PLAIN_SECONDS_RE = re.compile(r"^\d+(\.\d+)?$")
MAX_SECONDS = 24 * 3600


def parse_seconds(value: object) -> float:
    """Accept seconds as a number or text, or a timecode: H:MM:SS, M:SS or H:MM:SS.mmm.

    Raises ValueError when the value is not a time within one day.
    """
    seconds = _seconds_from(value)
    if seconds is None:
        raise ValueError(f"{value!r} is not a time")
    if seconds < 0 or seconds >= MAX_SECONDS:
        raise ValueError(f"{value!r} is outside one day")
    return seconds


def _seconds_from(value: object) -> float | None:
    """Number or text to seconds. None when the value is not a number or a timecode."""
    if isinstance(value, str):
        text = value.strip()
        if _PLAIN_SECONDS_RE.match(text):
            return float(text)
        match = _TIMECODE_RE.match(text)
        if not match:
            raise ValueError(f"{value!r} is not a timecode such as 0:28:00")
        hours, minutes, secs, fraction = match.groups()
        if int(minutes) > 59 or int(secs) > 59:
            raise ValueError(f"{value!r} has minutes or seconds above 59")
        seconds = int(hours or 0) * 3600 + int(minutes) * 60 + int(secs)
        return seconds + float(fraction) if fraction else float(seconds)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def clock(seconds: float) -> str:
    """Display form: 1:28:00 for an hour or more, 5:07 under an hour."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def timecode(seconds: float) -> str:
    """Transcript form: 0:28:00 (always H:MM:SS, as the recap and transcript use)."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def vtt_time(seconds: float) -> str:
    """WebVTT cue time: HH:MM:SS.mmm."""
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def iso_duration(seconds: float) -> str:
    """schema.org duration: PT5M27S."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    out = "PT"
    if hours:
        out += f"{hours}H"
    if minutes or hours:
        out += f"{minutes}M"
    return out + f"{secs}S"
