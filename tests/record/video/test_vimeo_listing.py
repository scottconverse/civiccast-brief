"""Vimeo RSS + yt-dlp/player fallback, against saved small fixtures."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from civiccast_brief.record.video.vimeo.listing import (
    VimeoLister,
    media_from_info,
    media_from_player_config,
    player_config_from_html,
    vimeo_ytdlp_argv,
)
from civiccast_brief.record.video.youtube.listing import (
    SOURCE_VIMEO_PLAYER,
    SOURCE_VIMEO_YTDLP,
    VideoError,
)

from .conftest import FIXTURE_FOLDER, FakeRunner, offline_client

FIRST = date(2026, 10, 17)
LAST = date(2026, 11, 16)


def fixture(name: str) -> bytes:
    return (FIXTURE_FOLDER / name).read_bytes()


def test_ytdlp_is_tried_first_then_the_public_player_config_is_used() -> None:
    requested: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        if request.url.path == "/svvsd/videos/rss":
            return httpx.Response(200, content=fixture("vimeo-svvsd.rss"))
        if request.url.path == "/video/222222222":
            return httpx.Response(200, content=fixture("vimeo-player-222.html"))
        return httpx.Response(404, text=request.url.path)

    runner = FakeRunner()
    runner.answers["111111111"] = (fixture("vimeo-info-111.json"), 0, "")
    runner.answers["222222222"] = (b"", 1, "ERROR: player unavailable")
    lister = VimeoLister(
        offline_client(transport=httpx.MockTransport(handle)),
        runner=runner,
        interpreter="python",
    )

    videos = lister.list_videos(FIRST, LAST)

    assert [video.video_id for video in videos] == ["111111111", "222222222"]
    assert videos[0].source_method == SOURCE_VIMEO_YTDLP
    assert videos[0].media_url == "https://cdn.example.test/111-720.mp4"
    assert videos[0].captions_url == "https://cdn.example.test/111.en.vtt"
    assert videos[1].source_method == SOURCE_VIMEO_PLAYER
    assert videos[1].media_url == "https://cdn.example.test/222-540.mp4"
    assert videos[1].captions_url == "https://cdn.example.test/222.en.vtt"
    assert [request.url.path for request in requested] == [
        "/svvsd/videos/rss",
        "/video/222222222",
    ]
    assert [call["argv"][-1] for call in runner.calls] == [
        "https://vimeo.com/111111111",
        "https://vimeo.com/222222222",
    ]


def test_the_ytdlp_command_has_no_cookie_or_login_options() -> None:
    argv = vimeo_ytdlp_argv("python", "https://vimeo.com/111111111")

    assert argv == [
        "python",
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--dump-single-json",
        "--no-warnings",
        "https://vimeo.com/111111111",
    ]
    assert not [part for part in argv if part in ("--cookies", "--username", "--password")]


def test_hls_and_text_tracks_are_read_when_no_progressive_url_exists() -> None:
    config = {
        "request": {
            "files": {
                "hls": {
                    "default_cdn": "fastly",
                    "cdns": {"fastly": {"url": "https://cdn.example.test/video.m3u8"}},
                }
            }
        },
        "video": {"duration": 90},
        "embed": {
            "text_tracks": [
                {"lang": "es", "kind": "captions", "url": "https://cdn.example.test/es.vtt"},
                {"lang": "en", "kind": "captions", "url": "https://cdn.example.test/en.vtt"},
            ]
        },
    }

    assert media_from_player_config(config) == (
        "https://cdn.example.test/video.m3u8",
        "https://cdn.example.test/en.vtt",
        90,
    )


def test_ytdlp_prefers_the_tallest_direct_mp4() -> None:
    info = {
        "duration": 20,
        "formats": [
            {"url": "https://cdn.test/low.mp4", "ext": "mp4", "protocol": "https",
             "mime_type": "video/mp4", "vcodec": "avc1", "height": 240},
            {"url": "https://cdn.test/high.mp4", "ext": "mp4", "protocol": "https",
             "mime_type": "video/mp4", "vcodec": "avc1", "height": 1080},
        ],
        "subtitles": {"en": [{"ext": "vtt", "url": "https://cdn.test/en.vtt"}]},
    }

    assert media_from_info(info) == ("https://cdn.test/high.mp4", "https://cdn.test/en.vtt", 20)


def test_a_player_page_without_config_is_a_failure() -> None:
    with pytest.raises(VideoError, match="playerConfig"):
        player_config_from_html("<html>no config</html>")


def test_a_date_window_with_no_items_returns_an_empty_list() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fixture("vimeo-svvsd.rss"))

    lister = VimeoLister(offline_client(transport=httpx.MockTransport(handle)), runner=FakeRunner())

    assert lister.list_videos(date(2026, 1, 1), date(2026, 1, 31)) == []
