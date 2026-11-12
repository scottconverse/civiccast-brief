"""A channel's newest videos: the Data API when a key is saved, then the feed, then its tabs.

The public feed (``/feeds/videos.xml``) lists 15 videos and some channels' feeds answer 404.
The Data API lists 50 in three calls of one quota unit each, but needs the key saved in the
credential store (:func:`civiccast_brief.record.credentials.youtube_api_key`). With a key, the
API is asked first and the feed is the fallback; without one, only the feed is asked. When
the feed fails too, the channel's own /streams and /videos pages are read (spec 8.1 step 3).
"""

from __future__ import annotations

from typing import Any

from civiccast_brief.record import credentials
from civiccast_brief.record.video.youtube.data_api import DataApiClient
from civiccast_brief.record.video.youtube.listing import VideoError, VideoListing
from civiccast_brief.record.video.youtube.rss import RssLister
from civiccast_brief.record.video.youtube.tab import ChannelTabLister


class ChannelLister:
    """Lists a channel over one :class:`httpx.Client` the caller owns."""

    def __init__(self, client: Any, key: str | None = None) -> None:
        self.api = DataApiClient(key, client) if key else None
        self.rss = RssLister(client)
        self.tab = ChannelTabLister(client)

    def list_videos(self, channel_id: str) -> list[VideoListing]:
        problems: list[str] = []
        steps = [lambda: self.rss.list_videos(channel_id), lambda: self.tab.list_videos(channel_id)]
        if self.api is not None:
            api = self.api
            steps.insert(0, lambda: api.list_channel_videos(channel_id=channel_id))
        for step in steps:
            try:
                return step()
            except VideoError as exc:
                problems.append(str(exc))
        raise VideoError("; then ".join(problems))


def channel_lister(client: Any) -> ChannelLister:
    """The lister for a real run: the saved key, if there is one."""
    return ChannelLister(client, credentials.youtube_api_key())
