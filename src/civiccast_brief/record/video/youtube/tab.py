"""A channel's tabs read as ordinary pages (spec 8.1 step 3).

``/channel/<id>/streams`` and ``/channel/<id>/videos`` are the pages anyone sees on the
channel. Each lists about 30 videos in its ``ytInitialData``, newest first, as
``lockupViewModel`` items (and as ``videoRenderer`` items in YouTube's older markup). This
reader takes the id, the title, the length from the thumbnail badge, and the status from
the badge or the "Scheduled for" line: "Upcoming" is upcoming, "LIVE" is live, a length is
a recording that is done. The page gives only relative dates ("1d ago"), so ``published``
is left empty and the meeting is dated from its title.

It needs no key and no tooling, so it is the step that still answers when the API has no
key, the feed answers 404 and yt-dlp is missing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import httpx

from civiccast_brief.record.video.youtube.listing import (
    SOURCE_CHANNEL_TAB,
    VideoError,
    VideoListing,
)
from civiccast_brief.record.video.youtube.page import get_page, initial_json

CHANNEL_TAB_URL = "https://www.youtube.com/channel/{channel_id}/{tab}"

#: The tabs read, in order: meetings are streamed, and some channels upload them instead.
TABS = ("streams", "videos")

_LENGTH = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def badge_length(text: str) -> int | None:
    """'1:18:30' is 4,710 seconds; anything else is no length."""
    match = _LENGTH.match(text.strip())
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return (hours * 60 + minutes) * 60 + seconds


def _walk(node: Any, key: str) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if isinstance(node.get(key), dict):
            yield node[key]
            return
        for value in node.values():
            yield from _walk(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, key)


def _texts(node: Any) -> Iterator[str]:
    """Every ``text``/``content``/``simpleText`` string under ``node``."""
    if isinstance(node, dict):
        for name, value in node.items():
            if name in ("text", "content", "simpleText", "label") and isinstance(value, str):
                yield value
            else:
                yield from _texts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _texts(value)


def _status(badges: list[str], lines: list[str]) -> tuple[str | None, int | None]:
    """``live_broadcast_content`` and ``duration_s`` from what the item shows."""
    words = [text.strip().upper() for text in badges]
    if "LIVE" in words or any(line.endswith("watching") for line in lines):
        return "live", None
    if "UPCOMING" in words or any(line.startswith("Scheduled for") for line in lines):
        return "upcoming", None
    for text in badges:
        length = badge_length(text)
        if length:
            return "none", length
    return None, None


def _lockup(item: dict[str, Any], channel_id: str) -> VideoListing | None:
    if item.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
        return None
    video_id = str(item.get("contentId") or "").strip()
    meta = (item.get("metadata") or {}).get("lockupMetadataViewModel") or {}
    title = str((meta.get("title") or {}).get("content") or "").strip()
    if not video_id or not title:
        return None
    rows = ((meta.get("metadata") or {}).get("contentMetadataViewModel") or {}).get(
        "metadataRows"
    ) or []
    lines = [text.strip() for text in _texts(rows)]
    badges = list(_texts(list(_walk(item.get("contentImage") or {}, "thumbnailBadgeViewModel"))))
    content, length = _status(badges, lines)
    return VideoListing(
        video_id=video_id,
        title=title,
        published=None,
        channel_id=channel_id,
        source_method=SOURCE_CHANNEL_TAB,
        duration_s=length,
        live_broadcast_content=content,
    )


def _renderer(item: dict[str, Any], channel_id: str) -> VideoListing | None:
    video_id = str(item.get("videoId") or "").strip()
    title = " ".join(_texts(item.get("title") or {})).strip()
    if not video_id or not title:
        return None
    lines = [text.strip() for text in _texts(item.get("publishedTimeText") or {})]
    lines += [text.strip() for text in _texts(item.get("upcomingEventData") or {})]
    if item.get("upcomingEventData"):
        lines.append("Scheduled for")
    badges = list(_texts(item.get("lengthText") or {}))
    badges += list(_texts(item.get("thumbnailOverlays") or []))
    content, length = _status(badges, lines)
    return VideoListing(
        video_id=video_id,
        title=title,
        published=None,
        channel_id=channel_id,
        source_method=SOURCE_CHANNEL_TAB,
        duration_s=length,
        live_broadcast_content=content,
    )


def parse_tab(html: str, channel_id: str) -> list[VideoListing]:
    """The videos one tab page lists, in page order."""
    data = initial_json(html, "ytInitialData")
    found = [_lockup(item, channel_id) for item in _walk(data, "lockupViewModel")]
    found += [_renderer(item, channel_id) for item in _walk(data, "videoRenderer")]
    return [video for video in found if video is not None]


class ChannelTabLister:
    """Reads a channel's ``/streams`` and ``/videos`` pages over an :class:`httpx.Client`."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def tab_url(self, channel_id: str, tab: str) -> str:
        return CHANNEL_TAB_URL.format(channel_id=channel_id, tab=tab)

    def list_videos(self, channel_id: str) -> list[VideoListing]:
        """Both tabs, the streams first, each video once."""
        if not channel_id:
            raise VideoError("the channel tabs need a channel id, and none was given")
        videos: dict[str, VideoListing] = {}
        problems = []
        for tab in TABS:
            url = self.tab_url(channel_id, tab)
            try:
                listed = parse_tab(get_page(self.client, url), channel_id)
            except VideoError as exc:
                problems.append(f"/{tab}: {exc}")
                continue
            for video in listed:
                videos.setdefault(video.video_id, video)
        if not videos:
            why = "; ".join(problems) or "both were read but list none"
            raise VideoError(f"the channel tabs listed 0 videos: {why}")
        return list(videos.values())
