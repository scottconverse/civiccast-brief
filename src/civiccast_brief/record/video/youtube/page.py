"""The JSON a public YouTube page carries, read over plain HTTP (spec 8.1 step 3, 8.2).

A channel tab (``/streams``, ``/videos``) and a watch page are ordinary HTML that anyone
can open. Each carries its data as a script assignment: ``var ytInitialData = {...};`` on
a channel tab, ``var ytInitialPlayerResponse = {...};`` on a watch page. This module finds
that object and turns it into a dict. No key, no sign-in, no cookies of the user's.

Checked 2026-11-11 on Boulder Valley School District's channel (UCbIqNnmnUXr1bWfNvYRyaAQ):
``/streams`` holds 30 ``lockupViewModel`` items; an upcoming meeting's watch page answers
``playabilityStatus.status = LIVE_STREAM_OFFLINE`` and a finished one ``OK`` with a length.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from civiccast_brief.record import pacing
from civiccast_brief.record.video.youtube.listing import VideoError
from civiccast_brief.record.video.youtube.readiness import (
    READINESS_FINISHED,
    READINESS_LIVE,
    READINESS_UNKNOWN,
    READINESS_UPCOMING,
)

WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

#: The page's words are English, so "Scheduled for" and "LIVE" read the same everywhere.
HEADERS = {"Accept-Language": "en-US,en;q=0.9"}

#: What a page read is called in the process pace's own log line.
PACE_WHAT = "a YouTube page"

#: The player status the spec names: a scheduled stream that has not started (spec 8.2).
PLAYER_UPCOMING = "LIVE_STREAM_OFFLINE"
PLAYER_OK = "OK"


def initial_json(html: str, name: str) -> dict[str, Any]:
    """The object assigned to ``name`` in the page, as a dict.

    The object is read with a JSON decoder from its first brace, so a ``};`` inside a
    title cannot cut it short.
    """
    for marker in (f"var {name} = ", f"{name} = ", f'window["{name}"] = '):
        start = html.find(marker)
        if start < 0:
            continue
        try:
            found, _ = json.JSONDecoder().raw_decode(html, start + len(marker))
        except ValueError as exc:
            raise VideoError(f"the page's {name} is not JSON this reader can read: {exc}") from exc
        if isinstance(found, dict):
            return found
    raise VideoError(f"the page carries no {name}")


def get_page(client: httpx.Client, url: str) -> str:
    """One public YouTube page, paced like every other YouTube request (spec 8.10)."""
    pacing.pacer().wait(what=PACE_WHAT)
    try:
        response = client.get(url, headers=HEADERS, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise VideoError(f"{url} could not be reached: {exc}") from exc
    if response.status_code != 200:
        if response.status_code == pacing.RATE_LIMIT_STATUS:
            pacing.pacer().rate_limited(what=PACE_WHAT, marker="HTTP 429")
        raise VideoError(f"{url} answered HTTP {response.status_code}")
    return response.text


class WatchPage:
    """Reads a video's player status from its public watch page (spec 8.2 step 2)."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def player_response(self, video_id: str) -> dict[str, Any]:
        html = get_page(self.client, WATCH_URL.format(video_id=video_id))
        return initial_json(html, "ytInitialPlayerResponse")


def player_readiness(player: Mapping[str, Any]) -> tuple[str, str]:
    """The readiness word a player response gives, and why (spec 8.2).

    ``LIVE_STREAM_OFFLINE`` is upcoming. ``OK`` while the broadcast is on is live.
    ``OK`` with a length and no broadcast on is finished. A finished stream whose
    length is still 0 is being processed, so it waits.
    """
    playability = player.get("playabilityStatus") or {}
    status = str(playability.get("status") or "")
    details = player.get("videoDetails") or {}
    micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    broadcast = micro.get("liveBroadcastDetails") or {}
    said = f"the player status is {status or 'missing'}"
    if status == PLAYER_UPCOMING:
        return READINESS_UPCOMING, f"{said}, so the stream has not started"
    if status != PLAYER_OK:
        reason = playability.get("reason")
        return READINESS_UNKNOWN, f"{said}" + (f" ({reason})" if reason else "")
    if details.get("isLive") or broadcast.get("isLiveNow"):
        return READINESS_LIVE, f"{said} and the broadcast is on now"
    try:
        length = int(details.get("lengthSeconds") or 0)
    except (TypeError, ValueError):
        length = 0
    if length > 0:
        return READINESS_FINISHED, f"{said} with a length of {length} seconds"
    return READINESS_UNKNOWN, f"{said} but the length is 0, so the recording is not ready"
