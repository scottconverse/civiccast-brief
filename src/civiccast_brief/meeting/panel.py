"""A meeting's transcript read from YouTube's "Show transcript" panel (spec 8.4.2).

When yt-dlp gets no captions for a video, the same transcript is still on the watch page for
anyone who opens it: expand the description, press "Show transcript", read the panel. This
does that in the installed Google Chrome (:mod:`civiccast_brief.citybuild.browser`), with no
sign-in and no cookies of the user's, and reads each line's timestamp and text. A line ends
where the next one starts; the last ends at the video's length.

Checked 2026-11-11 on Longmont's Planning and Zoning Commission of 21 October 2026
(unNcPhKJ45o): 1,144 lines from 0:01 to 2:28:34.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final, Protocol

#: The file the panel's lines are kept in, next to where yt-dlp's caption file would be.
SUFFIX: Final = "panel.json"
#: How long the last line runs when the video's length is not known.
LAST_LINE_MS: Final = 5_000
_STAMP = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


class PanelError(RuntimeError):
    """The panel could not be read, with the reason."""


class PanelBrowser(Protocol):
    def transcript_panel(self, url: str) -> dict[str, Any]:
        """``{"rows": [[timestamp, text], ...], "length": seconds or None}``."""
        ...


def stamp_ms(stamp: str) -> int:
    """'2:28:34' is 8,914,000 ms; '0:09' is 9,000 ms."""
    match = _STAMP.match(stamp.strip())
    if not match:
        raise PanelError(f"not a transcript timestamp: {stamp!r}")
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def segments(rows: list[list[str]], length_s: int | None) -> list[dict[str, Any]]:
    """The panel's rows as caption segments, in time order, empty lines dropped."""
    timed = sorted(
        (stamp_ms(stamp), " ".join(text.split())) for stamp, text in rows if text.strip()
    )
    out = []
    for i, (start, text) in enumerate(timed):
        if i + 1 < len(timed):
            end = timed[i + 1][0]
        elif length_s and length_s * 1000 > start:
            end = length_s * 1000
        else:
            end = start + LAST_LINE_MS
        out.append({"start_ms": start, "end_ms": end, "text": text})
    return out


def panel_file(work: Path, video_id: str) -> Path:
    return work / f"{video_id}.{SUFFIX}"


def read_panel(video_id: str, work: Path, browser: PanelBrowser | None = None) -> Path:
    """Read the panel into ``work/<id>.panel.json`` and return the file."""
    from civiccast_brief.citybuild import browser as chrome
    from civiccast_brief.citybuild.fetch import FetchError

    url = f"https://www.youtube.com/watch?v={video_id}"
    reader: Any = browser or chrome.shared()
    try:
        found = reader.transcript_panel(url)
    except FetchError as exc:
        raise PanelError(str(exc)) from exc
    lines = segments(found.get("rows") or [], found.get("length"))
    if not lines:
        raise PanelError(f"{url}: the transcript panel has no lines")
    path = panel_file(work, video_id)
    data = {"url": url, "origin": "youtube_transcript_panel", "segments": lines}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load(path: Path) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8"))["segments"])
