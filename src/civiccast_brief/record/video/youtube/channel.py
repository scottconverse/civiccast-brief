"""A channel's newest videos: the YouTube Data API when a key is saved, else the public feed.

The public feed (``/feeds/videos.xml``) lists 15 videos and some channels' feeds answer 404.
The Data API lists 50 in three calls of one quota unit each, but needs the key saved in the
credential store (:func:`civiccast_brief.record.credentials.youtube_api_key`). With a key, the
API is asked first and the feed is the fallback; without one, only the feed is asked.
"""

from __future__ import annotations

from typing import Any

from civiccast_brief.record import credentials
from civiccast_brief.record.video.youtube.data_api import DataApiClient
from civiccast_brief.record.video.youtube.listing import VideoError, VideoListing
from civiccast_brief.record.video.youtube.rss import RssLister


class ChannelLister:
    """Lists a channel over one :class:`httpx.Client` the caller owns."""

    def __init__(self, client: Any, key: str | None = None) -> None:
        self.api = DataApiClient(key, client) if key else None
        self.rss = RssLister(client)

    def list_videos(self, channel_id: str) -> list[VideoListing]:
        api_problem = ""
        if self.api is not None:
            try:
                return self.api.list_channel_videos(channel_id=channel_id)
            except VideoError as exc:
                api_problem = str(exc)
        try:
            return self.rss.list_videos(channel_id)
        except VideoError as exc:
            if api_problem:
                raise VideoError(f"{api_problem}; then {exc}") from exc
            raise


def channel_lister(client: Any) -> ChannelLister:
    """The lister for a real run: the saved key, if there is one."""
    return ChannelLister(client, credentials.youtube_api_key())
