"""Several cities in one desk window: one sign-in, one store each, and Add a city."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from civiccast_brief.review import web as web_module
from civiccast_brief.review.hub import make_hub_app

EMAIL = "editor@example.com"
PASSWORD = "desk-test-password"


class FakeStation:
    """Stands in for a city's Station so the test sees start/stop and runs nothing."""

    def __init__(self, *args, **kwargs) -> None:
        self.started = 0
        self.stopped = 0
        self.scheduled = False

    def status(self):
        return None

    def has_run(self, trigger: str, status: str) -> bool:
        return False

    def start(self) -> None:
        self.started += 1

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped += 1


@pytest.fixture
def hub_app(tmp_path, monkeypatch):
    monkeypatch.setattr(web_module, "Station", FakeStation)
    cities = tmp_path / "cities"
    for slug in ("alpha", "bravo"):
        (cities / slug / "city").mkdir(parents=True)
        (cities / slug / "runs" / "weekly").mkdir(parents=True)
    (cities / "alpha" / "city" / "city.json").write_text(
        json.dumps({"name": "Alpha City", "state": "Colorado"}), encoding="utf-8"
    )
    return make_hub_app(tmp_path / "hub.db", cities), tmp_path, cities


def sign_up(client: TestClient) -> TestClient:
    reply = client.post(
        "/sign-up",
        data={
            "token": client.app.state.hub.token,
            "email": EMAIL,
            "password": PASSWORD,
            "confirm": PASSWORD,
        },
        follow_redirects=False,
    )
    assert reply.status_code == 303, reply.text
    return client


def anon(app) -> TestClient:
    return TestClient(app, base_url="https://127.0.0.1")


def add_item(store, title: str) -> None:
    store.add_item(
        meeting_id="m1",
        title=title,
        meeting_date="2026-11-04",
        body="City Council",
        speakers={"voice 01": "unknown"},
        recap="Recap.",
        script="[S1] Script.",
    )


def test_one_sign_in_covers_every_city(hub_app):
    app, _, _ = hub_app
    with anon(app) as client:
        sign_up(client)
        cookie = client.cookies.get("__Host-ccb.session_token")
        assert cookie
        for path in ("/c/alpha/", "/c/bravo/"):
            page = client.get(path)
            assert page.status_code == 200, page.text
            assert "Items" in page.text


def test_each_city_shows_only_its_own_items(hub_app):
    app, _, _ = hub_app
    with anon(app) as client:
        sign_up(client)
        add_item(app.state.hub.desks["alpha"].store, "ALPHA ONLY STORY")
        assert "ALPHA ONLY STORY" in client.get("/c/alpha/").text
        assert "ALPHA ONLY STORY" not in client.get("/c/bravo/").text


def test_an_unknown_city_is_a_404(hub_app):
    app, _, _ = hub_app
    with anon(app) as client:
        sign_up(client)
        assert client.get("/c/nope/").status_code == 404


def test_add_a_city_creates_its_folders_and_it_answers_without_a_restart(hub_app):
    app, _, cities = hub_app
    with anon(app) as client:
        sign_up(client)
        added = client.post(
            "/cities/add",
            data={"token": app.state.hub.token, "name": "New Town", "state": "CO"},
            follow_redirects=False,
        )
        assert added.status_code == 303, added.text
        assert added.headers["location"] == "/c/new-town/sources/find?name=New+Town&state=CO"
        assert (cities / "new-town" / "city").is_dir()
        assert (cities / "new-town" / "runs" / "weekly").is_dir()
        page = client.get("/c/new-town/")
        assert page.status_code == 200, page.text
        find = client.get(added.headers["location"])
        assert 'value="New Town"' in find.text
        assert 'value="CO"' in find.text
        assert app.state.hub.desks["new-town"].station.started == 1


def test_a_bad_or_duplicate_name_is_refused_with_a_message(hub_app):
    app, _, _ = hub_app
    with anon(app) as client:
        sign_up(client)
        blank = client.post("/cities/add", data={"token": app.state.hub.token, "name": "   "})
        assert blank.status_code == 400
        assert "Enter the city&#39;s name" in blank.text
        duplicate = client.post("/cities/add", data={"token": app.state.hub.token, "name": "Alpha"})
        assert duplicate.status_code == 400
        assert "already here" in duplicate.text


def test_the_city_switcher_lists_every_city_on_a_city_page(hub_app):
    app, _, _ = hub_app
    with anon(app) as client:
        sign_up(client)
        page = client.get("/c/alpha/").text
        assert "City:" in page
        assert 'href="/c/alpha/"' in page
        assert 'href="/c/bravo/"' in page
        assert 'href="/">All cities</a>' in page
        assert "Alpha City" in page


def test_a_city_sign_in_redirect_stays_inside_the_city(hub_app):
    app, _, _ = hub_app
    with anon(app) as client:
        sign_up(client)
        reply = client.get("/c/alpha/sign-in", follow_redirects=False)
        assert reply.status_code == 303
        assert reply.headers["location"] == "/c/alpha/"


def test_the_hub_starts_and_stops_every_city_station(hub_app):
    app, _, _ = hub_app
    with anon(app):
        stations = [desk.station for desk in app.state.hub.desks.values()]
        assert len(stations) == 2
        assert all(s.started == 1 and s.stopped == 0 for s in stations)
    assert all(s.stopped == 1 for s in stations)
