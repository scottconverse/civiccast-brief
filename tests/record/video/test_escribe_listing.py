"""eScribe calendar + standalone-player parsing, against saved small fixtures."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from civiccast_brief.record.video.escribe.listing import (
    EScribeLister,
    isilive_media_url,
    parse_player,
)
from civiccast_brief.record.video.youtube.listing import SOURCE_ESCRIBE, VideoError

from .conftest import FIXTURE_FOLDER, offline_client

FIRST = date(2026, 10, 17)
LAST = date(2026, 11, 16)


def fixture(name: str) -> bytes:
    return (FIXTURE_FOLDER / name).read_bytes()


def test_calendar_post_keeps_only_meetings_with_video_and_builds_mp4_and_vtt() -> None:
    requested: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        if request.url.path.endswith("/MeetingsCalendarView.aspx/GetCalendarMeetings"):
            return httpx.Response(200, content=fixture("escribe-calendar.json"))
        if request.url.path.endswith("/Players/ISIStandAlonePlayer.aspx"):
            return httpx.Response(200, content=fixture("escribe-player-101.html"))
        return httpx.Response(404, text=request.url.path)

    lister = EScribeLister(offline_client(transport=httpx.MockTransport(handle)))
    videos = lister.list_videos(FIRST, LAST)

    assert len(videos) == 1
    video = videos[0]
    assert video.video_id == "meeting-101"
    assert video.title == "Board of County Commissioners - Regular Meeting"
    assert video.published == "2026/11/05 09:00:00"
    assert video.source_method == SOURCE_ESCRIBE
    assert video.media_url == (
        "https://video.isilive.ca/bouldercounty/Compact%20Encoder%20101_BOCC_2026-11-05.mp4"
    )
    assert video.captions_url == video.media_url + ".vtt"
    assert json.loads(requested[0].content) == {
        "calendarStartDate": FIRST.isoformat(),
        "calendarEndDate": LAST.isoformat(),
    }
    assert requested[1].url == httpx.URL(
        "https://pub-bouldercounty.escribemeetings.com/"
        "Players/ISIStandAlonePlayer.aspx?Id=meeting-101"
    )


def test_player_attributes_are_read_from_single_or_double_quotes() -> None:
    html = "<div id='isi_player' data-client_id='town' data-file_name='a b.mp4'></div>"

    assert parse_player(html) == ("town", "a b.mp4")
    assert isilive_media_url("town", "a b.mp4") == "https://video.isilive.ca/town/a%20b.mp4"


def test_a_player_without_media_attributes_is_a_failure() -> None:
    with pytest.raises(VideoError, match="client and file name"):
        parse_player("<html><body><div id='isi_player'></div></body></html>")


def test_a_calendar_with_no_video_link_returns_an_empty_list() -> None:
    lister = EScribeLister(offline_client())
    payload = fixture("escribe-calendar.json")
    rows = json.loads(payload)
    for row in rows["d"]:
        row["MeetingDocumentLink"] = []
    filtered = lister.parse_calendar(json.dumps(rows).encode(), FIRST, LAST)

    assert filtered == []
