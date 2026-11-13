"""The desk's Check button on the Sources page, and a YouTube handle on its form."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from civiccast_brief.citybuild import browser, cli
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app
from civiccast_brief.sources import add_source, load_sources, make_source

from .conftest import CHANNEL, HERE, SVCM, feed


@pytest.fixture
def desk(web, tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    with ReviewStore(tmp_path / "d.db") as store:
        app = make_desk_app(store, city_dir=city)
        app.state.desk.make_fetcher = web.fetcher
        yield TestClient(app, base_url="http://127.0.0.1"), city


def token(client) -> str:
    return client.app.state.desk.token


def add_channel(web, city) -> str:
    title = "City Council Regular Session 10/28/2026"
    web.add(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}",
        feed(("aaaaaaaaaaa", title, "2026-10-29T03:00:00+00:00")),
    )
    source = make_source("youtube", {"channel_id": CHANNEL, "bodies": ["City Council"]})
    add_source(city, source)
    return source.id


def test_the_sources_page_has_check_buttons_that_say_they_are_working(desk, web):
    client, city = desk
    assert "Check all sources" not in client.get("/sources").text  # no sources, nothing to check
    source_id = add_channel(web, city)
    page = client.get("/sources").text
    assert 'action="/sources/check"' in page
    assert page.count('data-busy="Checking… this reads') == 2  # all sources, and the one row
    assert f'name="source_id" value="{source_id}"' in page


def test_check_shows_read_and_not_read_per_body_and_saves_city_check_json(desk, web):
    client, city = desk
    add_channel(web, city)
    add_source(city, make_source("webpage", {"url": SVCM, "bodies": ["SVCM Board"]}))  # a 404
    response = client.post("/sources/check", data={"token": token(client)})
    assert response.status_code == 200
    text = response.text
    assert "1 of 2 body sources read" in text
    assert "<strong>READ</strong>" in text and "<strong>NOT READ</strong>" in text
    assert "2026-10-28" in text and "City Council Regular Session 10/28/2026" in text
    assert 'href="https://www.youtube.com/watch?v=aaaaaaaaaaa"' in text
    assert "Why:" in text
    saved = json.loads((city / cli.PROOF_FILE).read_text(encoding="utf-8"))
    assert [r["ok"] for r in saved["results"]] == [True, False]
    assert not any("captions" in url or "timedtext" in url for url in web.asked)


def test_check_of_one_source_reads_only_it_and_keeps_the_saved_file(desk, web):
    client, city = desk
    add_channel(web, city)
    other = make_source("webpage", {"url": SVCM, "bodies": ["SVCM Board"]})
    add_source(city, other)
    (city / cli.PROOF_FILE).write_text('{"keep": true}', encoding="utf-8")
    response = client.post("/sources/check", data={"token": token(client), "source_id": other.id})
    assert "0 of 1 body sources read" in response.text and "SVCM Board" in response.text
    assert "City Council" not in response.text
    assert web.asked == [SVCM]
    assert json.loads((city / cli.PROOF_FILE).read_text(encoding="utf-8")) == {"keep": True}


def test_check_names_what_is_wrong(desk, web):
    client, city = desk
    t = token(client)
    none = client.post("/sources/check", data={"token": t})
    assert none.status_code == 400 and "no sources to check" in none.text
    add_channel(web, city)
    gone = client.post("/sources/check", data={"token": t, "source_id": "youtube:nope"})
    assert gone.status_code == 404 and "No source youtube:nope" in gone.text
    assert client.post("/sources/check", data={"token": "wrong"}).status_code == 403


def test_check_runs_off_the_event_loop_and_closes_chrome_in_that_worker(desk, web, monkeypatch):
    client, city = desk
    add_channel(web, city)
    ran: dict[str, int] = {}
    real = cli.check_city

    def check(*a, **kw):
        ran["check"] = threading.get_ident()
        return real(*a, **kw)

    def close():
        ran["close"] = threading.get_ident()

    monkeypatch.setattr(cli, "check_city", check)
    monkeypatch.setattr(browser, "close_shared", close)
    client.post("/sources/check", data={"token": token(client)})
    assert ran["check"] == ran["close"]


def test_a_handle_or_channel_address_is_turned_into_the_channel_id(desk, web):
    client, city = desk
    web.add(
        "https://www.youtube.com/@CityofLoveland",
        (HERE / "youtube-cityofloveland-channel.html").read_bytes(),
    )
    added = client.post(
        "/sources/add",
        data={
            "token": token(client),
            "kind": "youtube",
            "channel_id": "https://www.youtube.com/@CityofLoveland",
            "bodies": "City Council",
        },
    )
    assert added.status_code == 200
    assert "UC9OMF1xKFd0h7Li6bBU9Awg" in added.text
    (source,) = load_sources(city)
    assert source.channel_id == "UC9OMF1xKFd0h7Li6bBU9Awg"
    assert web.asked == ["https://www.youtube.com/@CityofLoveland"]


def test_a_handle_that_cannot_be_read_says_what_to_paste_and_keeps_what_was_typed(desk, web):
    client, city = desk
    bad = client.post(
        "/sources/add",
        data={"token": token(client), "kind": "youtube", "channel_id": "@Nobody", "bodies": "X"},
    )
    assert bad.status_code == 400
    assert 'id="channel_id-error"' in bad.text and "Copy channel ID" in bad.text
    assert 'value="@Nobody"' in bad.text
    assert load_sources(city) == []
