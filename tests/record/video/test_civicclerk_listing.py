"""CivicClerk's Events + EventsMedia reader, against saved small JSON replies."""

from __future__ import annotations

from datetime import date

import httpx
from civiccast_brief.record.video.civicclerk.listing import (
    CivicClerkLister,
    civicclerk_api,
    events_url,
)
from civiccast_brief.record.video.youtube.listing import (
    SOURCE_CIVICCLERK,
    VideoError,
    VideoListing,
)

from .conftest import FIXTURE_FOLDER, offline_client

FIRST = date(2026, 10, 17)
LAST = date(2026, 11, 16)


def fixture(name: str) -> bytes:
    return (FIXTURE_FOLDER / name).read_bytes()


def make_lister() -> tuple[CivicClerkLister, list[httpx.Request]]:
    requested: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        path = request.url.path
        if path.endswith("/Events"):
            page = 2 if "skiptoken" in str(request.url) else 1
            return httpx.Response(200, content=fixture(f"civicclerk-events-page{page}.json"))
        if path.endswith("/EventsMedia/101"):
            return httpx.Response(200, content=fixture("civicclerk-media-101.json"))
        if path.endswith("/EventsMedia/102"):
            return httpx.Response(200, content=fixture("civicclerk-media-102.json"))
        if path.endswith("/EventsMedia/103"):
            return httpx.Response(200, content=fixture("civicclerk-media-103.json"))
        return httpx.Response(404, text=path)

    return (
        CivicClerkLister(offline_client(transport=httpx.MockTransport(handle))),
        requested,
    )


def test_the_events_request_has_the_odata_range_order_and_top() -> None:
    url = httpx.URL(events_url(civicclerk_api("erieco"), FIRST, LAST))

    assert url.host == "erieco.api.civicclerk.com"
    assert url.params["$filter"] == (
        "startDateTime ge 2026-10-17T00:00:00Z and startDateTime le 2026-11-16T23:59:59Z"
    )
    assert url.params["$orderby"] == "startDateTime desc"
    assert url.params["$top"] == "50"


def test_the_lister_follows_next_link_and_keeps_only_direct_mp4_media() -> None:
    lister, requested = make_lister()

    videos = lister.list_videos(FIRST, LAST)

    assert videos == [
        VideoListing(
            video_id="101",
            title="Town Council Meeting",
            published="2026-11-10T19:30:00Z",
            channel_id="Town Council",
            source_method=SOURCE_CIVICCLERK,
            duration_s=8100,
            live_status="finished",
            media_url="https://cpmedia.azureedge.net/erieco/council-2026-11-10.mp4",
        )
    ]
    assert [request.url.path for request in requested] == [
        "/v1/Events",
        "/v1/EventsMedia/101",
        "/v1/EventsMedia/102",
        "/v1/Events",
        "/v1/EventsMedia/103",
    ]


def test_a_different_town_selects_a_different_api_host() -> None:
    lister = CivicClerkLister(offline_client(), tenant="othertown")

    assert lister.base_url == "https://othertown.api.civicclerk.com/v1"


def test_a_media_api_failure_names_the_event_and_status() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Events"):
            return httpx.Response(200, content=fixture("civicclerk-events-page1.json"))
        return httpx.Response(503, text="down")

    lister = CivicClerkLister(offline_client(transport=httpx.MockTransport(handle)))

    try:
        lister.list_videos(FIRST, LAST)
    except VideoError as exc:
        assert "event 101" in str(exc)
        assert "503" in str(exc)
    else:
        raise AssertionError("a failed media read must not be a quiet skip")
