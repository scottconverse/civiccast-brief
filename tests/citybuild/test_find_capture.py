"""The city.json stub Find writes names the video platform Find found, never YouTube by default."""

import json

from desk_signin import signed_in

from civiccast_brief.citybuild.build import build_city
from civiccast_brief.citybuild.search import Search
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app
from civiccast_brief.sources import make_source

CHANNEL = "UCH5_wkpLrKYb1JuUk6-UdNg"
BODIES = ["City Council"]
CABLE = make_source("cablecast", {"host": "reflect-ex.cablecast.tv", "bodies": BODIES})
YOUTUBE = make_source("youtube", {"channel_id": CHANNEL, "bodies": BODIES})
GRANICUS = make_source("granicus", {"host": "ex.granicus.com", "view_id": "2", "bodies": BODIES})
PRIME = make_source("primegov", {"url": "https://ex.primegov.com", "bodies": BODIES})


def _capture(tmp_path, *sources):
    found = Search(name="Exville", state="Colorado")
    for source in sources:
        found.add(source, "test")
    build_city(tmp_path, found)
    return json.loads((tmp_path / "city.json").read_text(encoding="utf-8"))["capture"]


def test_a_cablecast_only_city_is_not_written_as_youtube(tmp_path):
    assert _capture(tmp_path, CABLE, PRIME) == {
        "platform": "cablecast",
        "host": "reflect-ex.cablecast.tv",
    }


def test_the_one_platform_found_is_the_one_written(tmp_path):
    assert _capture(tmp_path / "y", YOUTUBE) == {"platform": "youtube"}
    assert _capture(tmp_path / "g", GRANICUS) == {
        "platform": "granicus",
        "host": "ex.granicus.com",
        "view_id": 2,
    }


def test_no_platform_found_writes_none(tmp_path):
    assert _capture(tmp_path, PRIME) == {"platform": "none"}


def test_several_platforms_are_left_to_the_city_page(tmp_path):
    assert _capture(tmp_path, YOUTUBE, CABLE) == {"platform": "none"}
    with ReviewStore(tmp_path / "d.db") as store:
        client = signed_in(make_desk_app(store, city_dir=tmp_path))
        page = client.get("/city")
        assert "Find found more than one video platform (youtube, cablecast)" in page.text
        reply = client.post(
            "/city",
            data={
                "token": client.app.state.desk.token,
                "name": "Exville",
                "slug": "exville",
                "state": "CO",
                "contact_email": "a@example.org",
                "podcast_email": "b@example.org",
                "platform": "cablecast",
            },
        )
        assert reply.status_code == 200 and "Saved to" in reply.text
        assert "more than one video platform" not in reply.text
        # A platform with no source behind it is refused, and nothing changes.
        bad = client.post(
            "/city",
            data={
                "token": client.app.state.desk.token,
                "name": "Exville",
                "slug": "exville",
                "state": "CO",
                "contact_email": "a@example.org",
                "podcast_email": "b@example.org",
                "platform": "granicus",
            },
        )
        assert bad.status_code == 400 and "no Granicus source" in bad.text
    capture = json.loads((tmp_path / "city.json").read_text(encoding="utf-8"))["capture"]
    assert capture == {"platform": "cablecast", "host": "reflect-ex.cablecast.tv"}
