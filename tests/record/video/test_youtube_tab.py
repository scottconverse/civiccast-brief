"""The channel tabs read as pages (spec 8.1 step 3) and the watch page's player status (8.2).

Fixtures: Boulder Valley School District's ``/streams`` and ``/videos`` tabs and two watch
pages, read over plain HTTP on 2026-11-11 and cut down to the fields these readers use.
"""

from __future__ import annotations

import httpx
import pytest
from civiccast_brief.record.video.youtube.channel import ChannelLister
from civiccast_brief.record.video.youtube.listing import SOURCE_CHANNEL_TAB, VideoError
from civiccast_brief.record.video.youtube.page import (
    WatchPage,
    initial_json,
    player_readiness,
)
from civiccast_brief.record.video.youtube.readiness import (
    READINESS_FINISHED,
    READINESS_LIVE,
    READINESS_UNKNOWN,
    READINESS_UPCOMING,
)
from civiccast_brief.record.video.youtube.status import StatusAsker
from civiccast_brief.record.video.youtube.tab import ChannelTabLister, badge_length, parse_tab

from .conftest import FIXTURE_FOLDER, offline_client

BVSD = "UCbIqNnmnUXr1bWfNvYRyaAQ"
UPCOMING = "y-xlQWT-Y6E"
FINISHED = "xHzdfYR1Jxc"


def page(name: str) -> str:
    return (FIXTURE_FOLDER / name).read_text(encoding="utf-8")


def serve(pages: dict[str, tuple[int, str]]) -> tuple[httpx.Client, list[str]]:
    """A client that answers each path from ``pages``; anything else is a 404."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        asked.append(key)
        status, body = pages.get(key, (404, "not here"))
        return httpx.Response(status, text=body)

    return offline_client(transport=httpx.MockTransport(handle)), asked


def tabs(streams: tuple[int, str] | None = None, videos: tuple[int, str] | None = None):
    return {
        f"/channel/{BVSD}/streams": streams or (200, page(f"tab-{BVSD}-streams.html")),
        f"/channel/{BVSD}/videos": videos or (200, page(f"tab-{BVSD}-videos.html")),
    }


# -- the tab page ----------------------------------------------------------------


def test_a_streams_tab_gives_each_meeting_with_its_status_and_length() -> None:
    videos = parse_tab(page(f"tab-{BVSD}-streams.html"), BVSD)

    assert [video.video_id for video in videos] == [
        UPCOMING,
        "bMqOTdjycHQ",
        FINISHED,
        "sxB7gqQltMg",
        "1Msea5EdtSc",
    ]
    first, done = videos[0], videos[2]
    assert first.title == "8 de diciembre - Reunión del Consejo de Educación del BVSD"
    assert first.live_broadcast_content == "upcoming"
    assert first.readiness() == READINESS_UPCOMING
    assert first.duration_s is None
    assert done.live_broadcast_content == "none"
    assert done.duration_s == 4342, "the badge 1:12:22"
    assert done.readiness() == READINESS_FINISHED
    assert {video.source_method for video in videos} == {SOURCE_CHANNEL_TAB}
    assert {video.channel_id for video in videos} == {BVSD}
    assert all(video.published is None for video in videos), "the page gives only '1d ago'"


def test_the_older_video_renderer_markup_is_read_too() -> None:
    html = (
        '<script>var ytInitialData = {"items": ['
        '{"videoRenderer": {"videoId": "old00000001", "title": {"runs": [{"text": "Board"}, '
        '{"text": " Meeting"}]}, "lengthText": {"simpleText": "1:02:03"}}},'
        '{"videoRenderer": {"videoId": "old00000002", "title": {"simpleText": "Next Meeting"},'
        ' "upcomingEventData": {"startTime": "1800000000"}}},'
        '{"videoRenderer": {"videoId": "old00000003", "title": {"simpleText": "Now"},'
        ' "thumbnailOverlays": [{"thumbnailOverlayTimeStatusRenderer": {"text": '
        '{"simpleText": "LIVE"}}}]}}]};</script>'
    )
    videos = parse_tab(html, BVSD)

    assert [(v.video_id, v.title, v.readiness()) for v in videos] == [
        ("old00000001", "Board  Meeting", READINESS_FINISHED),
        ("old00000002", "Next Meeting", READINESS_UPCOMING),
        ("old00000003", "Now", READINESS_LIVE),
    ]
    assert videos[0].duration_s == 3723


def test_a_title_holding_the_scripts_end_does_not_cut_the_data_short() -> None:
    html = '<script>var ytInitialData = {"t": "a};</script> b", "n": 2};</script>'
    assert initial_json(html, "ytInitialData") == {"t": "a};</script> b", "n": 2}


def test_a_page_with_no_data_says_so() -> None:
    with pytest.raises(VideoError, match="carries no ytInitialData"):
        parse_tab("<html>consent</html>", BVSD)


@pytest.mark.parametrize(
    ("text", "seconds"), [("1:12:22", 4342), ("7:21", 441), ("Upcoming", None), ("LIVE", None)]
)
def test_the_badge_is_a_length_only_when_it_reads_as_one(text: str, seconds: int | None) -> None:
    assert badge_length(text) == seconds


# -- the lister ------------------------------------------------------------------


def test_the_lister_reads_streams_then_videos_and_lists_each_video_once() -> None:
    client, asked = serve(tabs())

    videos = ChannelTabLister(client).list_videos(BVSD)

    assert asked == [f"/channel/{BVSD}/streams", f"/channel/{BVSD}/videos"]
    ids = [video.video_id for video in videos]
    assert len(ids) == 8, "5 streams, 3 uploads; the stream both tabs list is listed once"
    assert ids.count(FINISHED) == 1
    assert ids[:5] == [UPCOMING, "bMqOTdjycHQ", FINISHED, "sxB7gqQltMg", "1Msea5EdtSc"]


def test_one_tab_failing_still_gives_the_other() -> None:
    client, _ = serve(tabs(streams=(500, "oops")))
    videos = ChannelTabLister(client).list_videos(BVSD)
    assert len(videos) == 4


def test_both_tabs_failing_is_a_failure_with_both_reasons() -> None:
    client, _ = serve(tabs(streams=(404, ""), videos=(200, "<html>no data</html>")))
    with pytest.raises(VideoError) as caught:
        ChannelTabLister(client).list_videos(BVSD)
    message = str(caught.value)
    assert message.startswith("the channel tabs listed 0 videos")
    assert "/streams" in message and "HTTP 404" in message
    assert "/videos" in message and "carries no ytInitialData" in message


def test_the_channel_lister_reads_the_tabs_when_the_feed_answers_404() -> None:
    client, asked = serve(tabs())

    videos = ChannelLister(client).list_videos(BVSD)

    assert asked[0] == f"/feeds/videos.xml?channel_id={BVSD}"
    assert asked[1:] == [f"/channel/{BVSD}/streams", f"/channel/{BVSD}/videos"]
    assert len(videos) == 8
    assert videos[0].source_method == SOURCE_CHANNEL_TAB


def test_the_channel_lister_names_every_reader_when_all_fail() -> None:
    client, _ = serve({})
    with pytest.raises(VideoError) as caught:
        ChannelLister(client).list_videos(BVSD)
    message = str(caught.value)
    assert "the YouTube feed answered HTTP 404" in message
    assert "; then the channel tabs listed 0 videos" in message


# -- the player status -----------------------------------------------------------


def watch(video_id: str) -> WatchPage:
    client, _ = serve({f"/watch?v={video_id}": (200, page(f"watch-{video_id}.html"))})
    return WatchPage(client)


def test_a_scheduled_stream_reads_upcoming_from_its_watch_page() -> None:
    word, reason = player_readiness(watch(UPCOMING).player_response(UPCOMING))
    assert word == READINESS_UPCOMING
    assert reason == "the player status is LIVE_STREAM_OFFLINE, so the stream has not started"


def test_a_finished_stream_reads_finished_from_its_watch_page() -> None:
    word, reason = player_readiness(watch(FINISHED).player_response(FINISHED))
    assert word == READINESS_FINISHED
    assert reason == "the player status is OK with a length of 4342 seconds"


@pytest.mark.parametrize(
    ("player", "word"),
    [
        ({"playabilityStatus": {"status": "OK"}, "videoDetails": {"isLive": True}}, "live"),
        (
            {
                "playabilityStatus": {"status": "OK"},
                "videoDetails": {"lengthSeconds": "0"},
                "microformat": {
                    "playerMicroformatRenderer": {"liveBroadcastDetails": {"isLiveNow": True}}
                },
            },
            "live",
        ),
        (
            {"playabilityStatus": {"status": "OK"}, "videoDetails": {"lengthSeconds": "0"}},
            "unknown",
        ),
        ({"playabilityStatus": {"status": "ERROR", "reason": "Video unavailable"}}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_the_player_status_gives_one_word(player: dict, word: str) -> None:
    assert player_readiness(player)[0] == word


class NoYtdlp:
    """A runner that fails the test if yt-dlp is started."""

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("yt-dlp was started after the player status answered")


def test_the_status_ask_uses_the_watch_page_before_ytdlp() -> None:
    asker = StatusAsker(runner=NoYtdlp(), interpreter="python", js_runtime="node")
    asker.player = watch(UPCOMING)

    answer = asker.ask(video_id=UPCOMING, url=f"https://www.youtube.com/watch?v={UPCOMING}")

    assert answer.readiness == READINESS_UPCOMING
    assert [ask.method for ask in answer.asks] == ["data_api", "player"]
    assert (
        answer.reason == "the player status is LIVE_STREAM_OFFLINE, so the stream has not started"
    )


def test_a_watch_page_that_cannot_be_read_is_a_reason_and_ytdlp_is_asked() -> None:
    client, _ = serve({})
    asker = StatusAsker(runner=NoYtdlp(), interpreter="python", js_runtime="node")
    asker.player = WatchPage(client)

    ask = asker._ask_player(FINISHED, "")  # noqa: SLF001
    assert ask.readiness == READINESS_UNKNOWN
    assert not ask.answered
    assert ask.reason.startswith("the page read failed: ")
    assert "HTTP 404" in ask.reason
