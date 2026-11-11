"""The weekly run lists a YouTube channel through the Data API when a key is saved.

Longmont's public feed answered HTTP 404 on 2026-11-10, so a run that read only the feed found
no videos on a YouTube source.
"""

from __future__ import annotations

import httpx

from civiccast_brief.record import credentials
from civiccast_brief.weekly.__main__ import real_listers

CHANNEL = "UCH5_wkpLrKYb1JuUk6-UdNg"


def handle(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host != "www.googleapis.com":
        return httpx.Response(404)
    if path.endswith("/channels"):
        uploads = {"relatedPlaylists": {"uploads": "UUH5_wkpLrKYb1JuUk6-UdNg"}}
        return httpx.Response(200, json={"items": [{"contentDetails": uploads}]})
    if path.endswith("/playlistItems"):
        item = {
            "snippet": {"title": "City Council Regular Session 10/28/2026", "channelId": CHANNEL},
            "contentDetails": {
                "videoId": "aaaaaaaaaaa",
                "videoPublishedAt": "2026-10-29T03:00:00Z",
            },
        }
        return httpx.Response(200, json={"items": [item]})
    return httpx.Response(200, json={"items": []})


def test_a_youtube_source_is_listed_through_the_data_api_when_the_feed_answers_404(monkeypatch):
    monkeypatch.setattr(credentials, "read_credential", lambda target: "fake-key-not-real")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        videos = real_listers(client).youtube(CHANNEL)

    assert [(v.video_id, v.title) for v in videos] == [
        ("aaaaaaaaaaa", "City Council Regular Session 10/28/2026")
    ]
