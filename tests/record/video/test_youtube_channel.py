"""A channel's videos: the Data API when a key is saved, the public feed when not.

Longmont's feed answered HTTP 404 on 2026-11-10 while its channel was live, so a city setup and
a weekly run that only read the feed found no videos.
"""

from __future__ import annotations

import httpx
import pytest
from civiccast_brief.record import credentials
from civiccast_brief.record.video.youtube.channel import channel_lister
from civiccast_brief.record.video.youtube.listing import SOURCE_DATA_API, SOURCE_RSS, VideoError

from .conftest import CITY_CHANNEL_ID, TEST_API_KEY, FakeDataApi, offline_client


def client(api: FakeDataApi, feed: bytes, feed_status: int = 200) -> tuple[httpx.Client, list]:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        if request.url.host == "www.googleapis.com":
            return api.handle(request)
        return httpx.Response(feed_status, content=feed)

    return offline_client(transport=httpx.MockTransport(handle)), asked


@pytest.fixture
def saved_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "read_credential", lambda target: TEST_API_KEY)


def test_with_a_saved_key_the_api_lists_the_channel_and_the_feed_is_not_asked(
    api: FakeDataApi, city_rss: bytes, saved_key: None
) -> None:
    http, asked = client(api, city_rss, feed_status=404)

    videos = channel_lister(http).list_videos(CITY_CHANNEL_ID)

    assert videos[0].video_id == "jhsFsEz0P5A"
    assert {v.source_method for v in videos} == {SOURCE_DATA_API}
    assert set(asked) == {"www.googleapis.com"}


def test_when_the_api_fails_the_feed_is_read(
    api: FakeDataApi, city_rss: bytes, saved_key: None
) -> None:
    api.channels_status = 500
    http, asked = client(api, city_rss)

    videos = channel_lister(http).list_videos(CITY_CHANNEL_ID)

    assert videos and {v.source_method for v in videos} == {SOURCE_RSS}
    assert asked == ["www.googleapis.com", "www.youtube.com"]


def test_with_no_key_only_the_feed_is_read(api: FakeDataApi, city_rss: bytes) -> None:
    http, asked = client(api, city_rss)

    videos = channel_lister(http).list_videos(CITY_CHANNEL_ID)

    assert videos and asked == ["www.youtube.com"]


def test_when_both_fail_the_error_names_both(
    api: FakeDataApi, city_rss: bytes, saved_key: None
) -> None:
    api.channels_status = 500
    http, _ = client(api, b"", feed_status=404)

    with pytest.raises(VideoError) as caught:
        channel_lister(http).list_videos(CITY_CHANNEL_ID)

    assert "Data API answered HTTP 500" in str(caught.value)
    assert "feed answered HTTP 404" in str(caught.value)
    assert TEST_API_KEY not in str(caught.value)
