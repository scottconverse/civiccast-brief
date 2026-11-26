"""The setup wizard: city lines, Find and Check jobs, rosters, copying, and the step gates."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from civiccast_brief import setup_check
from civiccast_brief.citybuild.read import Proof
from civiccast_brief.review import setup_wizard, site_settings
from civiccast_brief.review import web as web_module
from civiccast_brief.review.hub import make_hub_app
from civiccast_brief.review.setup_wizard import SetupJobs

EMAIL = "editor@example.com"
PASSWORD = "desk-test-password"


class FakeStation:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def status(self):
        return None

    def has_run(self, trigger: str, status: str) -> bool:
        return False

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> None:
        pass

    def set_publish(self, steps) -> None:
        pass


class RecordingStation(FakeStation):
    """Records the publish steps the desk builds it with, and every later replacement."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.publish = [list(step) for step in kwargs.get("publish", [])]
        self.set_calls: list[list[list[str]]] = []

    def set_publish(self, steps) -> None:
        self.publish = [list(step) for step in steps]
        self.set_calls.append(self.publish)


def write_city(cities, slug: str, *, name: str = "", source: bool = False) -> None:
    (cities / slug / "city").mkdir(parents=True, exist_ok=True)
    (cities / slug / "runs" / "weekly").mkdir(parents=True, exist_ok=True)
    if name:
        (cities / slug / "city" / "city.json").write_text(
            json.dumps({"name": name, "state": "Colorado"}), encoding="utf-8"
        )
    if source:
        (cities / slug / "city" / "sources.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "sources": [
                        {
                            "kind": "webpage",
                            "url": "https://example.org/meetings",
                            "bodies": ["City Council"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )


@pytest.fixture
def hub_app(tmp_path, monkeypatch):
    monkeypatch.setattr(web_module, "Station", FakeStation)
    cities = tmp_path / "cities"
    write_city(cities, "alpha", name="Alpha City")
    app = make_hub_app(tmp_path / "hub.db", cities)
    return app, cities


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


def client_for(app) -> TestClient:
    client = TestClient(app, base_url="https://127.0.0.1")
    return sign_up(client)


def token(client: TestClient) -> str:
    return client.app.state.hub.token


def wait(jobs: SetupJobs, seconds: float = 5.0) -> None:
    end = time.monotonic() + seconds
    while jobs.running() and time.monotonic() < end:
        time.sleep(0.01)
    assert not jobs.running(), "the background job did not finish"


def ready_checks(*, signin=True, voice=True):
    return [
        setup_check.Check("ffmpeg", "ffmpeg", "required", True, "On PATH.", ""),
        setup_check.Check(
            "claude_signin",
            "Claude sign-in",
            "required",
            True if signin else None,
            "Claude answered." if signin else "Not checked yet.",
            "" if signin else "Press Check Claude sign-in.",
        ),
        setup_check.Check(
            "gemini",
            "Gemini voice",
            "voice",
            voice,
            "A key is set." if voice else "No key.",
            "" if voice else "Set GEMINI_API_KEY.",
        ),
        setup_check.Check("lfm3", "LFM3 voice", "voice", False, "Not found.", "Install it."),
    ]


# ----- the Cities textarea -------------------------------------------------------------------


def test_a_city_line_takes_city_state_and_an_optional_website():
    good, bad = setup_wizard.parse_cities(
        "Longmont, CO\nLyons, Colorado, https://www.lyonscolorado.com\n"
    )
    assert bad == []
    assert [(c.name, c.state, c.website) for c in good] == [
        ("Longmont", "Colorado", ""),
        ("Lyons", "Colorado", "https://www.lyonscolorado.com"),
    ]


def test_bad_lines_report_their_line_number_and_why():
    good, bad = setup_wizard.parse_cities(
        "\nLongmont, CO\nBogus, ZZ\nLyons, CO, not-a-link\n, CO\nEstes Park\n"
    )
    assert [c.name for c in good] == ["Longmont"]
    assert [(e.line, e.text) for e in bad] == [
        (3, "Bogus, ZZ"),
        (4, "Lyons, CO, not-a-link"),
        (5, ", CO"),
        (6, "Estes Park"),
    ]
    assert "not a US state" in bad[0].why
    assert "http:// or https://" in bad[1].why
    assert "name is missing" in bad[2].why
    assert "state is missing" in bad[3].why


def test_blank_lines_are_skipped_and_do_not_count(hub_app):
    app, _ = hub_app
    client = client_for(app)
    page = client.post(
        "/setup/cities",
        data={"token": token(client), "cities": "\n\nLongmont, CO\n\n"},
    )
    assert page.status_code == 200
    assert "Added: Longmont." in page.text


def test_the_page_adds_good_lines_and_shows_bad_ones(hub_app):
    app, cities = hub_app
    client = client_for(app)
    page = client.post(
        "/setup/cities",
        data={"token": token(client), "cities": "Longmont, CO\nBogus, ZZ\nLyons, CO"},
    )
    assert page.status_code == 400  # a bad line asks for a fix, but the good ones are saved
    assert "Line 2" in page.text and "Bogus, ZZ" in page.text
    assert (cities / "longmont" / "city").is_dir()
    assert (cities / "lyons" / "city").is_dir()
    saved = json.loads((cities / "longmont" / "setup.json").read_text(encoding="utf-8"))
    assert saved == {"name": "Longmont", "state": "Colorado", "website": ""}


def test_a_city_already_here_is_kept_and_reported_not_refused(hub_app):
    app, cities = hub_app
    client = client_for(app)
    page = client.post(
        "/setup/cities",
        data={
            "token": token(client),
            "cities": "Alpha, CO, https://www.alpha.example",
        },
    )
    assert page.status_code == 200
    assert "Already here, so kept: Alpha." in page.text
    saved = json.loads((cities / "alpha" / "setup.json").read_text(encoding="utf-8"))
    assert saved["website"] == "https://www.alpha.example"


# ----- Find ----------------------------------------------------------------------------------


def test_find_runs_each_city_once_and_skips_cities_that_have_sources(hub_app):
    app, cities = hub_app
    write_city(cities, "bravo", name="Bravo Town")
    write_city(cities, "charlie", name="Charlie City", source=True)
    client = client_for(app)
    called: list[str] = []

    def fake_find(city, *, fetcher, open_page):
        called.append(city.slug)
        write_city(cities, city.slug, source=True)  # what build_city would save
        return 2

    app.state.hub.setup_jobs = SetupJobs(
        find_city=fake_find,
        fetcher_factory=lambda: object(),
        open_page=lambda url: None,
        close_chrome=lambda: None,
    )
    started = client.post(
        "/setup/find", data={"token": token(client), "action": "find"}, follow_redirects=False
    )
    assert started.status_code == 303 and started.headers["location"] == "/setup/find"
    wait(app.state.hub.setup_jobs)
    assert sorted(called) == ["alpha", "bravo"]  # charlie already had a source

    page = client.get("/setup/find")
    assert "Saved 2 sources" in page.text
    assert "Next: Check sources" in page.text


def test_find_shows_progress_rows_and_refreshes_while_it_runs(hub_app):
    app, _ = hub_app
    client = client_for(app)
    release = threading.Event()

    def slow_find(city, *, fetcher, open_page):
        release.wait(2.0)
        return 1

    app.state.hub.setup_jobs = SetupJobs(
        find_city=slow_find,
        fetcher_factory=lambda: object(),
        open_page=lambda url: None,
        close_chrome=lambda: None,
    )
    client.post("/setup/find", data={"token": token(client), "action": "find"})
    end = time.monotonic() + 2.0
    while app.state.hub.setup_jobs.status()[0].state == "waiting" and time.monotonic() < end:
        time.sleep(0.01)
    page = client.get("/setup/find")
    assert 'http-equiv="refresh"' in page.text
    assert "Searching…" in page.text
    release.set()
    wait(app.state.hub.setup_jobs)
    page = client.get("/setup/find")
    assert 'http-equiv="refresh"' not in page.text
    assert "Saved 1 source" in page.text


def test_a_failed_city_does_not_stop_the_others(hub_app):
    app, cities = hub_app
    write_city(cities, "bravo", name="Bravo Town")
    client = client_for(app)

    def failing_find(city, *, fetcher, open_page):
        if city.slug == "alpha":
            raise RuntimeError("the site did not answer")
        return 1

    app.state.hub.setup_jobs = SetupJobs(
        find_city=failing_find,
        fetcher_factory=lambda: object(),
        open_page=lambda url: None,
        close_chrome=lambda: None,
    )
    client.post("/setup/find", data={"token": token(client), "action": "find"})
    wait(app.state.hub.setup_jobs)
    page = client.get("/setup/find")
    assert "Failed: the site did not answer" in page.text
    assert "Saved 1 source" in page.text


# ----- Check ---------------------------------------------------------------------------------


def proof(*, ok: bool) -> Proof:
    proof = Proof(body="City Council", kind="webpage", address="https://example.org/m")
    proof.ok = ok
    if ok:
        proof.meeting_date = __import__("datetime").date(2026, 11, 4)
        proof.title = "Regular meeting"
        proof.what = "video"
        proof.document = "https://example.org/video"
    else:
        proof.problem = "the page listed no meetings"
    return proof


def test_check_saves_city_check_json_per_city(hub_app, monkeypatch):
    app, cities = hub_app
    write_city(cities, "bravo", name="Bravo Town", source=True)
    client = client_for(app)

    def fake_check_city(folder, *, captions=False, fetcher=None):
        return [proof(ok=True)]

    monkeypatch.setattr("civiccast_brief.citybuild.cli.check_city", fake_check_city)
    app.state.hub.setup_jobs = SetupJobs(
        fetcher_factory=lambda: object(), close_chrome=lambda: None
    )
    client.post("/setup/check", data={"token": token(client), "action": "check"})
    wait(app.state.hub.setup_jobs)
    for slug in ("alpha", "bravo"):
        saved = json.loads((cities / slug / "city" / "city-check.json").read_text())
        assert saved["results"][0]["ok"] is True


def test_check_blocks_next_on_a_not_read_body(hub_app, monkeypatch):
    app, _ = hub_app
    client = client_for(app)

    def fake_check_city(folder, *, captions=False, fetcher=None):
        return [proof(ok=False)]

    monkeypatch.setattr("civiccast_brief.citybuild.cli.check_city", fake_check_city)
    app.state.hub.setup_jobs = SetupJobs(
        fetcher_factory=lambda: object(), close_chrome=lambda: None
    )
    client.post("/setup/check", data={"token": token(client), "action": "check"})
    wait(app.state.hub.setup_jobs)
    page = client.get("/setup/check")
    assert "NOT READ" in page.text
    assert "the page listed no meetings" in page.text
    assert "Next: Rosters" not in page.text
    assert 'href="/c/alpha/sources"' in page.text


def test_check_allows_next_when_every_body_is_read(hub_app):
    app, cities = hub_app
    client = client_for(app)
    (cities / "alpha" / "city" / "city-check.json").write_text(
        json.dumps({"checked": "2026-11-24", "results": [{"body": "City Council", "ok": True}]}),
        encoding="utf-8",
    )
    page = client.get("/setup/check")
    assert "READ" in page.text
    assert "Next: Rosters" in page.text


def test_leave_this_city_out_moves_the_folder_and_never_deletes_it(hub_app):
    app, cities = hub_app
    client = client_for(app)
    page = client.post(
        "/setup/check",
        data={"token": token(client), "action": "leave-out", "slug": "alpha"},
        follow_redirects=False,
    )
    assert page.status_code == 303
    assert not (cities / "alpha").exists()
    moved = cities / "_left-out" / "alpha"
    assert (moved / "city").is_dir()
    assert (moved / "city" / "city.json").is_file()
    assert "alpha" not in app.state.hub.slugs()


def test_leaving_out_a_missing_city_is_refused(hub_app):
    app, _ = hub_app
    client = client_for(app)
    page = client.post(
        "/setup/check",
        data={"token": token(client), "action": "leave-out", "slug": "nope"},
    )
    assert page.status_code == 404 and "No city" in page.text


def test_leaving_out_waits_while_find_or_check_runs(hub_app):
    app, cities = hub_app
    client = client_for(app)
    release = threading.Event()

    def slow_check(folder, *, captions=False, fetcher=None):
        release.wait(2.0)
        return []

    app.state.hub.setup_jobs = SetupJobs(
        check_city=slow_check, fetcher_factory=lambda: object(), close_chrome=lambda: None
    )
    client.post("/setup/check", data={"token": token(client), "action": "check"})
    end = time.monotonic() + 2.0
    while not app.state.hub.setup_jobs.running() and time.monotonic() < end:
        time.sleep(0.01)
    assert app.state.hub.setup_jobs.running()

    page = client.post(
        "/setup/check",
        data={"token": token(client), "action": "leave-out", "slug": "alpha"},
    )
    assert page.status_code == 409
    assert "Wait until the current Find or Check finishes" in page.text
    assert (cities / "alpha" / "city").is_dir()
    assert not (cities / "_left-out" / "alpha").exists()

    release.set()
    wait(app.state.hub.setup_jobs)


def test_a_failed_move_puts_the_city_back_and_says_why(hub_app, monkeypatch):
    app, cities = hub_app
    client = client_for(app)
    real_rename = type(cities).rename

    def broken_rename(self, target):
        if self.name == "alpha":
            raise OSError("the folder is in use")
        return real_rename(self, target)

    monkeypatch.setattr(type(cities), "rename", broken_rename)
    page = client.post(
        "/setup/check",
        data={"token": token(client), "action": "leave-out", "slug": "alpha"},
    )
    assert page.status_code == 500
    assert "Could not move alpha: the folder is in use" in page.text
    assert (cities / "alpha" / "city").is_dir()
    assert "alpha" in app.state.hub.slugs()
    city_page = client.get("/c/alpha/")
    assert city_page.status_code == 200


# ----- Rosters and copying -------------------------------------------------------------------


def test_rosters_next_needs_one_roster_file_per_city(hub_app):
    app, cities = hub_app
    client = client_for(app)
    blocked = client.get("/setup/rosters")
    assert "Next: Models and voices" not in blocked.text
    assert "no roster file yet" in blocked.text
    (cities / "alpha" / "city" / "roster.json").write_text(
        json.dumps({"body": "City Council", "members": [{"name": "Ada"}]}), encoding="utf-8"
    )
    allowed = client.get("/setup/rosters")
    assert "Next: Models and voices" in allowed.text
    assert "City Council" in allowed.text and ">1<" in allowed.text


def spec_for(cities, slug: str = "alpha", name: str = "Alpha City") -> setup_wizard.CitySpec:
    return setup_wizard.CitySpec(slug, cities / slug, setup_wizard.CitySetup(name, "Colorado"))


ROSTER_LINE = "Planning Commission: wrote planning-commission.json, 7 members from https://x"


def test_the_rosters_job_saves_its_lines_and_counts_the_bodies(tmp_path):
    cities = tmp_path / "cities"
    write_city(cities, "alpha", name="Alpha City")
    jobs = SetupJobs(
        find_rosters=lambda city, **kwargs: [ROSTER_LINE],
        fetcher_factory=lambda: object(),
        close_chrome=lambda: None,
    )
    assert jobs.start_rosters([spec_for(cities)]) is True
    wait(jobs)
    row = jobs.status()[0]
    assert (row.state, row.count) == ("found", 1)
    saved = json.loads((cities / "alpha" / "city" / "roster-find.json").read_text())
    assert saved["lines"] == [ROSTER_LINE]
    assert saved["day"]


def test_a_roster_search_that_fails_says_why(tmp_path):
    cities = tmp_path / "cities"
    write_city(cities, "alpha", name="Alpha City")

    def failing_find(city, **kwargs):
        raise RuntimeError("the board page did not answer")

    jobs = SetupJobs(
        find_rosters=failing_find,
        fetcher_factory=lambda: object(),
        close_chrome=lambda: None,
    )
    jobs.start_rosters([spec_for(cities)])
    wait(jobs)
    row = jobs.status()[0]
    assert row.state == "failed"
    assert "the board page did not answer" in row.message


def test_a_second_roster_search_while_one_runs_is_refused(tmp_path):
    cities = tmp_path / "cities"
    write_city(cities, "alpha", name="Alpha City")
    release = threading.Event()

    def slow_find(city, **kwargs):
        release.wait(2.0)
        return []

    jobs = SetupJobs(
        find_rosters=slow_find,
        fetcher_factory=lambda: object(),
        close_chrome=lambda: None,
    )
    assert jobs.start_rosters([spec_for(cities)]) is True
    assert jobs.start_rosters([spec_for(cities)]) is False
    release.set()
    wait(jobs)


def test_the_rosters_page_lists_every_body_and_says_what_is_missing(hub_app):
    app, cities = hub_app
    city = cities / "alpha" / "city"
    (city / "sources.json").write_text(
        json.dumps(
            {
                "format": 1,
                "sources": [
                    {
                        "kind": "youtube",
                        "channel_id": "UCabcdefghijklmnopqrstuv",
                        "bodies": ["Town Board", "Planning Commission"],
                    },
                    {
                        "kind": "webpage",
                        "url": "https://example.org/meetings",
                        "bodies": ["town  board"],  # the same body, other spacing and case
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (city / "roster.json").write_text(
        json.dumps({"body": "Town Board", "members": [{"name": "Ada"}]}), encoding="utf-8"
    )
    client = client_for(app)
    page = client.get("/setup/rosters")
    assert page.text.count("Town Board") == 1  # the repeated body is dropped
    assert "Planning Commission" in page.text
    assert "No roster" in page.text
    assert "Its meetings cannot be written until it has members." in page.text
    assert 'href="/c/alpha/roster"' in page.text
    assert ROSTER_LINE not in page.text

    (city / "roster-find.json").write_text(
        json.dumps({"day": "2026-11-25", "lines": [ROSTER_LINE]}), encoding="utf-8"
    )
    page = client.get("/setup/rosters")
    assert ROSTER_LINE in page.text


def test_find_board_rosters_starts_the_job_only_with_the_token(hub_app):
    app, _ = hub_app
    client = client_for(app)
    calls: list[str] = []

    def fake_find(city, **kwargs):
        calls.append(city.slug)
        return [ROSTER_LINE]

    app.state.hub.setup_jobs = SetupJobs(
        find_rosters=fake_find,
        fetcher_factory=lambda: object(),
        close_chrome=lambda: None,
    )
    refused = client.post("/setup/rosters", data={"action": "rosters"}, follow_redirects=False)
    assert refused.status_code == 403
    started = client.post(
        "/setup/rosters",
        data={"token": token(client), "action": "rosters"},
        follow_redirects=False,
    )
    assert started.status_code == 303 and started.headers["location"] == "/setup/rosters"
    wait(app.state.hub.setup_jobs)
    assert calls == ["alpha"]
    assert app.state.hub.setup_jobs.status()[0].state == "found"


def test_copy_settings_writes_only_missing_or_different_files_and_spares_the_source(tmp_path):
    source = tmp_path / "cities" / "alpha" / "city"
    same = tmp_path / "cities" / "bravo" / "city"
    missing = tmp_path / "cities" / "charlie" / "city"
    for folder in (source, same, missing):
        folder.mkdir(parents=True)
    (source / "models.json").write_text("models", encoding="utf-8")
    (source / "voices.json").write_text("voices", encoding="utf-8")
    (same / "models.json").write_text("models", encoding="utf-8")  # already identical
    (missing / "models.json").write_text("older", encoding="utf-8")  # different
    before = (source / "models.json").read_bytes(), (source / "voices.json").read_bytes()

    lines = setup_wizard.copy_settings(source, [same, missing])

    assert "bravo: copied voices.json." in lines
    assert "charlie: copied models.json, voices.json." in lines
    assert (same / "models.json").read_text(encoding="utf-8") == "models"
    assert (missing / "models.json").read_text(encoding="utf-8") == "models"
    assert (missing / "voices.json").read_text(encoding="utf-8") == "voices"
    assert ((source / "models.json").read_bytes(), (source / "voices.json").read_bytes()) == before


def test_the_copy_button_shows_what_it_did(hub_app):
    app, cities = hub_app
    write_city(cities, "bravo", name="Bravo Town")
    (cities / "alpha" / "city" / "models.json").write_text("{}", encoding="utf-8")
    client = client_for(app)
    page = client.post("/setup/models-voices", data={"token": token(client), "action": "copy"})
    assert page.status_code == 200
    assert "Bravo Town: copied models.json." in page.text
    assert (cities / "bravo" / "city" / "models.json").read_text(encoding="utf-8") == "{}"


# ----- the Website step -----------------------------------------------------------------------


def website_form(client: TestClient, **values) -> dict[str, str]:
    data = {
        "token": token(client),
        "action": "save",
        "target": "none",
        "base_url": "",
        "media_base_url": "",
        "out": "",
        "media_out": "",
        "project": "",
        "bucket": "",
        "prefix": "",
        "account": "",
    }
    data.update(values)
    return data


def test_the_website_page_offers_the_three_choices_and_a_row_per_city(hub_app):
    app, cities = hub_app
    write_city(cities, "bravo", name="Bravo Town")
    client = client_for(app)
    page = client.get("/setup/website")
    assert page.status_code == 200
    for choice in ("none", "folder", "cloudflare"):
        assert f'value="{choice}"' in page.text
    assert "Alpha City" in page.text and "Bravo Town" in page.text
    assert (
        "cmdkey /generic:CivicCastBrief/cloudflare-deploy /user:token /pass:YOUR-TOKEN" in page.text
    )
    assert page.text.count("data-busy=") >= 2
    assert 'href="/setup/test">Next: Test' in page.text


def test_saving_the_website_writes_each_city_s_site_json_with_the_slug_filled_in(hub_app):
    app, cities = hub_app
    write_city(cities, "bravo", name="Bravo Town")
    client = client_for(app)
    reply = client.post(
        "/setup/website",
        data=website_form(
            client,
            target="cloudflare",
            base_url="https://{slug}.example.org",
            media_base_url="https://media.example.org/{slug}",
            project="civiccast-brief-{slug}",
            bucket="media-bucket",
            prefix="{slug}/",
        ),
        follow_redirects=False,
    )
    assert reply.status_code == 303
    assert reply.headers["location"] == "/setup/website?notice=saved"
    for slug in ("alpha", "bravo"):
        saved = json.loads((cities / slug / "site.json").read_text(encoding="utf-8"))
        assert saved["target"] == "cloudflare"
        assert saved["base_url"] == f"https://{slug}.example.org"
        assert saved["media_base_url"] == f"https://media.example.org/{slug}"
        assert saved["project"] == f"civiccast-brief-{slug}"
        assert saved["prefix"] == f"{slug}/"


def test_a_bad_website_value_is_shown_per_city_and_nothing_is_saved(hub_app):
    app, cities = hub_app
    write_city(cities, "bravo", name="Bravo Town")
    client = client_for(app)
    page = client.post(
        "/setup/website",
        data=website_form(
            client,
            target="folder",
            base_url="longmont.example.org",
            out="web",
            media_out="",
        ),
    )
    assert page.status_code == 400
    assert "Alpha City" in page.text and "Bravo Town" in page.text
    assert "must start with http:// or https://" in page.text
    assert "must be a full path" in page.text
    assert not (cities / "alpha" / "site.json").exists()
    assert not (cities / "bravo" / "site.json").exists()


def test_none_is_saved_and_next_is_always_allowed(hub_app):
    app, cities = hub_app
    client = client_for(app)
    reply = client.post(
        "/setup/website", data=website_form(client, target="none"), follow_redirects=False
    )
    assert reply.status_code == 303
    saved = json.loads((cities / "alpha" / "site.json").read_text(encoding="utf-8"))
    assert saved["target"] == "none"
    page = client.get("/setup/website")
    assert 'href="/"' in page.text


def test_check_cloudflare_reports_only_whether_the_token_and_npx_exist(hub_app, monkeypatch):
    app, _ = hub_app
    client = client_for(app)
    secret = "super-secret-token-value"
    monkeypatch.setattr(site_settings, "credential_exists", lambda: bool(secret))
    monkeypatch.setattr(site_settings, "npx_present", lambda: False)
    page = client.post(
        "/setup/website", data=website_form(client, action="check", target="cloudflare")
    )
    assert page.status_code == 200
    assert "Cloudflare deploy token" in page.text
    assert "found" in page.text and "not found" in page.text  # token found, npx missing
    assert secret not in page.text


def test_saving_the_website_updates_the_running_station(hub_app, monkeypatch):
    app, _cities = hub_app
    monkeypatch.setattr(web_module, "Station", RecordingStation)
    client = client_for(app)
    assert client.get("/c/alpha/").status_code == 200
    station = app.state.hub.desks["alpha"].station
    assert station.publish == []  # no site.json yet

    client.post(
        "/setup/website",
        data=website_form(
            client,
            target="cloudflare",
            base_url="https://{slug}.example.org",
            media_base_url="https://media.example.org/{slug}",
            project="civiccast-brief-{slug}",
            bucket="media-bucket",
            prefix="{slug}/",
        ),
    )
    assert station.set_calls
    build, deploy = station.publish
    assert build[:4] == [sys.executable, "-m", "civiccast_brief.weekly", "publish"]
    assert deploy[:5] == [
        sys.executable,
        "-m",
        "civiccast_brief.station",
        "deploy",
        "cloudflare",
    ]
    assert "--project" in deploy and "civiccast-brief-alpha" in deploy


# ----- the step list and its gates -----------------------------------------------------------


def test_setup_redirects_to_the_first_unfinished_step(hub_app, monkeypatch):
    app, _cities = hub_app
    client = client_for(app)

    monkeypatch.setattr(setup_check, "run_checks", lambda **kw: ready_checks(signin=False))
    # alpha has no sources yet.
    reply = client.get("/setup", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == "/setup/find"

    monkeypatch.setattr(
        setup_check,
        "run_checks",
        lambda **kw: [
            setup_check.Check("ffmpeg", "ffmpeg", "required", False, "No.", "Install ffmpeg.")
        ],
    )
    reply = client.get("/setup", follow_redirects=False)
    assert reply.headers["location"] == "/setup/computer"


def test_setup_reaches_the_last_step_when_every_file_is_in_place(hub_app, monkeypatch):
    app, cities = hub_app
    client = client_for(app)
    monkeypatch.setattr(setup_check, "run_checks", lambda **kw: ready_checks())
    write_city(cities, "alpha", name="Alpha City", source=True)
    (cities / "alpha" / "city" / "city-check.json").write_text(
        json.dumps({"results": [{"ok": True}]}), encoding="utf-8"
    )
    (cities / "alpha" / "city" / "roster.json").write_text(
        json.dumps({"body": "City Council", "members": []}), encoding="utf-8"
    )
    reply = client.get("/setup", follow_redirects=False)
    assert reply.headers["location"] == "/setup/models-voices"


def test_the_computer_step_blocks_next_until_the_sign_in_is_checked(hub_app, monkeypatch):
    app, _ = hub_app
    client = client_for(app)
    monkeypatch.setattr(setup_check, "run_checks", lambda **kw: ready_checks(signin=False))
    page = client.get("/setup/computer")
    assert "Next: Cities" not in page.text
    assert "Claude sign-in (press Check Claude sign-in)" in page.text

    monkeypatch.setattr(
        setup_check,
        "run_checks",
        lambda **kw: ready_checks(signin=kw.get("claude_signin", False)),
    )
    checked = client.post("/setup/computer", data={"token": token(client), "claude": "1"})
    assert "Claude sign-in checked." in checked.text
    assert "Next: Cities" in checked.text


def test_the_computer_step_needs_one_voice_engine(hub_app, monkeypatch):
    app, _ = hub_app
    client = client_for(app)
    monkeypatch.setattr(setup_check, "run_checks", lambda **kw: ready_checks(voice=False))
    page = client.post("/setup/computer", data={"token": token(client), "claude": "1"})
    assert "Next: Cities" not in page.text
    assert "a ready voice engine" in page.text


def test_the_step_list_marks_the_current_step(hub_app):
    app, _ = hub_app
    client = client_for(app)
    page = client.get("/setup/rosters")
    assert '<span aria-current="step">5 Rosters</span>' in page.text
    assert 'href="/setup/find">3 Find sources</a>' in page.text


def test_the_city_list_links_to_setup(hub_app):
    app, _ = hub_app
    client = client_for(app)
    assert 'href="/setup"' in client.get("/").text


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("post", "/setup/computer", {}),
        ("post", "/setup/cities", {"cities": "Longmont, CO"}),
        ("post", "/setup/find", {"action": "find"}),
        ("post", "/setup/check", {"action": "check"}),
        ("post", "/setup/check", {"action": "leave-out", "slug": "alpha"}),
        ("post", "/setup/rosters", {"action": "rosters"}),
        ("post", "/setup/models-voices", {"action": "copy"}),
        ("post", "/setup/website", {"action": "save", "target": "none"}),
        ("post", "/setup/test", {"action": "test"}),
        (
            "post",
            "/setup/weekly",
            {"action": "save", "day": "monday", "time": "06:00", "timezone": "America/Denver"},
        ),
        ("post", "/setup/weekly", {"action": "install"}),
        ("post", "/setup/weekly", {"action": "remove"}),
    ],
)
def test_every_post_needs_the_form_token(hub_app, method, path, data):
    app, _ = hub_app
    client = client_for(app)
    response = getattr(client, method)(path, data=data, follow_redirects=False)
    assert response.status_code == 403
    assert "form token" in response.text


def test_the_wizard_needs_a_sign_in(hub_app):
    app, _ = hub_app
    anon = TestClient(app, base_url="https://127.0.0.1")
    reply = anon.get("/setup", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == "/sign-in"


# ----- the Test step -------------------------------------------------------------------------


class RunStation(FakeStation):
    """A Station whose Test runs finish at once and whose start/end order is recorded."""

    EVENTS: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, db, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.db = db
        self.slug = Path(db).parent.name
        self.publish = [list(step) for step in kwargs.get("publish", [])]
        self.ran: list[list[list[str]]] = []
        self.reconfigured: list[dict] = []
        self._runs: dict[int, SimpleNamespace] = {}
        self._ended: set[int] = set()
        self._next = 1

    def run_now_with(self, steps, trigger: str = "test") -> int:
        run_id = self._next
        self._next += 1
        self.ran.append([list(step) for step in steps])
        self._runs[run_id] = SimpleNamespace(
            id=run_id, trigger=trigger, status="ok", log="> the command\nan item is ready"
        )
        type(self).EVENTS.append(("start", self.slug))
        return run_id

    def run(self, run_id: int):
        run = self._runs.get(run_id)
        if run is not None and run_id not in self._ended:
            self._ended.add(run_id)
            type(self).EVENTS.append(("end", self.slug))
        return run

    def runs(self, limit: int = 20) -> list[SimpleNamespace]:
        return sorted(self._runs.values(), key=lambda item: item.id, reverse=True)[:limit]

    def reconfigure(self, **kwargs) -> None:
        self.reconfigured.append(kwargs)


@pytest.fixture
def rich_hub_app(tmp_path, monkeypatch):
    monkeypatch.setattr(web_module, "Station", RunStation)
    RunStation.EVENTS = []
    cities = tmp_path / "cities"
    write_city(cities, "alpha", name="Alpha City")
    write_city(cities, "bravo", name="Bravo Town")
    app = make_hub_app(tmp_path / "hub.db", cities)
    return app, cities


def add_review_item(app, slug: str, meeting: str) -> None:
    app.state.hub.load(slug).store.add_item(
        meeting_id=meeting,
        title="A meeting",
        meeting_date="2026-11-04",
        body="City Council",
        speakers={"voice 01": "unknown"},
        recap="Recap.",
        script="[S1] Script.",
    )


def task_runner(*, installed: bool = False):
    """A fake PowerShell: answers the status call and records install and remove argv."""
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        script = argv[-1]
        if "Register-ScheduledTask" in script:
            return 0, "", ""
        if "Unregister-ScheduledTask" in script:
            return 0, "removed", ""
        if installed:
            return (
                0,
                json.dumps(
                    {
                        "installed": True,
                        "state": "Ready",
                        "next": "2026-11-30T06:00:00-07:00",
                        "last": "",
                        "result": 267011,
                    }
                ),
                "",
            )
        return 0, '{"installed": false}', ""

    return runner, calls


def test_test_every_city_runs_one_at_a_time_with_the_desk_s_test_steps(rich_hub_app):
    app, _ = rich_hub_app
    add_review_item(app, "alpha", "a1")
    add_review_item(app, "bravo", "b1")
    client = client_for(app)
    assert "Waiting" in client.get("/setup/test").text

    reply = client.post(
        "/setup/test", data={"token": token(client), "action": "test"}, follow_redirects=False
    )
    assert reply.status_code == 303 and reply.headers["location"] == "/setup/test"
    wait(app.state.hub.test_jobs)

    assert RunStation.EVENTS == [
        ("start", "alpha"),
        ("end", "alpha"),
        ("start", "bravo"),
        ("end", "bravo"),
    ]
    page = client.get("/setup/test")
    assert page.text.count("OK — a meeting waits for review") == 2
    assert "Next: Every week" in page.text

    steps = app.state.hub.desks["alpha"].station.ran[0]
    assert "--limit" in steps[0] and "--review-only" in steps[0]
    assert "run" in steps[0] and "civiccast_brief.weekly" in steps[0]


def test_a_city_is_done_only_with_an_ok_run_and_a_review_item(rich_hub_app):
    app, _ = rich_hub_app
    add_review_item(app, "alpha", "a1")  # bravo has no item waiting
    client = client_for(app)
    client.post("/setup/test", data={"token": token(client), "action": "test"})
    wait(app.state.hub.test_jobs)

    page = client.get("/setup/test")
    assert "OK — a meeting waits for review" in page.text
    assert "no item waits for review" in page.text.lower()
    assert "Next: Every week" not in page.text


def test_the_test_step_points_at_the_run_and_the_city_s_items(rich_hub_app):
    app, _ = rich_hub_app
    add_review_item(app, "alpha", "a1")
    add_review_item(app, "bravo", "b1")
    client = client_for(app)
    client.post("/setup/test", data={"token": token(client), "action": "test"})
    wait(app.state.hub.test_jobs)

    page = client.get("/setup/test").text
    assert 'href="/c/alpha/runs/1"' in page
    assert 'href="/c/alpha/">Items</a>' in page


# ----- the Every week step -------------------------------------------------------------------


def test_every_week_saves_each_city_and_reconfigures_its_station(rich_hub_app):
    app, cities = rich_hub_app
    app.state.hub.task_runner, _calls = task_runner(installed=True)
    client = client_for(app)
    client.get("/c/alpha/")
    client.get("/c/bravo/")
    (cities / "alpha" / "city" / "weekly.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
                "days": 7,
                "diarize": False,
            }
        ),
        encoding="utf-8",
    )

    reply = client.post(
        "/setup/weekly",
        data={
            "token": token(client),
            "action": "save",
            "day": "tuesday",
            "time": "07:30",
            "timezone": "America/Denver",
            "diarize": "1",
        },
        follow_redirects=False,
    )
    assert reply.status_code == 303
    assert reply.headers["location"] == "/setup/weekly?notice=saved"

    for slug, days in (("alpha", 7), ("bravo", 14)):
        saved = json.loads((cities / slug / "city" / "weekly.json").read_text(encoding="utf-8"))
        assert saved["enabled"] is True
        assert saved["schedule"] == {
            "day": "tuesday",
            "time": "07:30",
            "timezone": "America/Denver",
        }
        assert saved["diarize"] is True
        assert saved["days"] == days

    station = app.state.hub.desks["alpha"].station
    assert station.reconfigured
    call = station.reconfigured[-1]
    assert call["scheduled"] is True
    assert call["schedule"].weekday == 1  # Tuesday
    assert "civiccast_brief.weekly" in call["steps"][0]


def test_the_task_buttons_call_powershell_and_show_its_status(rich_hub_app):
    app, _ = rich_hub_app
    runner, calls = task_runner(installed=False)
    app.state.hub.task_runner = runner
    client = client_for(app)
    assert "Not set up" in client.get("/setup/weekly").text

    installed = client.post(
        "/setup/weekly", data={"token": token(client), "action": "install"}, follow_redirects=False
    )
    assert installed.headers["location"] == "/setup/weekly?notice=installed"
    assert "Register-ScheduledTask" in calls[-1][-1]

    removed = client.post(
        "/setup/weekly", data={"token": token(client), "action": "remove"}, follow_redirects=False
    )
    assert removed.headers["location"] == "/setup/weekly?notice=removed"
    assert "Unregister-ScheduledTask" in calls[-1][-1]


def test_every_week_is_finished_when_the_task_is_set_and_every_schedule_is_on(rich_hub_app):
    app, _ = rich_hub_app
    app.state.hub.task_runner, _calls = task_runner(installed=True)
    client = client_for(app)
    client.post(
        "/setup/weekly",
        data={
            "token": token(client),
            "action": "save",
            "day": "monday",
            "time": "06:00",
            "timezone": "America/Denver",
        },
    )
    page = client.get("/setup/weekly")
    assert "Next: Everything works" in page.text
    assert 'href="/setup/done"' in page.text
    assert page.text.count('<span class="status ok">On</span>') == 2


# ----- the Everything works step -------------------------------------------------------------


@pytest.fixture
def done_hub_app(tmp_path, monkeypatch):
    """A hub whose Test runs finish at once, with the PC checks and the Windows task OK."""
    monkeypatch.setattr(web_module, "Station", RunStation)
    RunStation.EVENTS = []
    cities = tmp_path / "cities"
    write_city(cities, "alpha", name="Alpha City")
    write_city(cities, "bravo", name="Bravo Town")
    app = make_hub_app(tmp_path / "hub.db", cities)
    app.state.hub.task_runner, _calls = task_runner(installed=True)
    monkeypatch.setattr(setup_check, "run_checks", lambda **kw: ready_checks())
    return app, cities


def prepare_everything_ok(app, cities, client: TestClient) -> None:
    """Give both cities every file the page reads, then run the Test step once."""
    for slug in ("alpha", "bravo"):
        write_city(cities, slug, source=True)
        city = cities / slug / "city"
        (city / "city-check.json").write_text(
            json.dumps({"results": [{"body": "City Council", "ok": True}]}), encoding="utf-8"
        )
        (city / "roster.json").write_text(
            json.dumps({"body": "City Council", "members": [{"name": "Ada"}]}),
            encoding="utf-8",
        )
        (city / "weekly.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "schedule": {
                        "day": "monday",
                        "time": "06:00",
                        "timezone": "America/Denver",
                    },
                    "days": 14,
                    "diarize": False,
                }
            ),
            encoding="utf-8",
        )
        add_review_item(app, slug, f"{slug}-1")
    client.post("/setup/test", data={"token": token(client), "action": "test"})
    wait(app.state.hub.test_jobs)


def test_everything_works_says_so_when_every_row_is_ok(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)

    page = client.get("/setup/done")
    assert page.status_code == 200
    assert "Everything works." in page.text
    assert "Needs work" not in page.text
    assert page.text.count('<span class="status ok">OK</span>') == 12  # 2 PC rows + 5 per city


def test_no_sources_needs_work_and_links_to_the_city_s_sources(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    (cities / "alpha" / "city" / "sources.json").unlink()

    page = client.get("/setup/done")
    assert "1 thing needs work." in page.text
    assert "No sources are saved yet." in page.text
    assert 'href="/c/alpha/sources"' in page.text


def test_a_not_read_body_is_named_and_needs_work(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    (cities / "alpha" / "city" / "city-check.json").write_text(
        json.dumps({"results": [{"body": "Parks Board", "ok": False}]}), encoding="utf-8"
    )

    page = client.get("/setup/done")
    assert "NOT READ: Parks Board." in page.text
    assert "1 thing needs work." in page.text
    assert 'href="/c/alpha/sources"' in page.text


def test_no_test_run_yet_needs_work_and_links_to_the_test_step(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    write_city(cities, "charlie", name="Charlie City", source=True)
    city = cities / "charlie" / "city"
    (city / "city-check.json").write_text(
        json.dumps({"results": [{"body": "City Council", "ok": True}]}), encoding="utf-8"
    )
    (city / "roster.json").write_text(
        json.dumps({"body": "City Council", "members": [{"name": "Ada"}]}), encoding="utf-8"
    )
    (city / "weekly.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
                "days": 14,
                "diarize": False,
            }
        ),
        encoding="utf-8",
    )

    page = client.get("/setup/done")
    assert "No test run yet." in page.text
    assert 'href="/setup/test"' in page.text


def test_a_failed_test_run_shows_its_message_and_needs_work(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    # Charlie is added after the Test job, so no job row hides its station's own failed run.
    write_city(cities, "charlie", name="Charlie City", source=True)
    city = cities / "charlie" / "city"
    (city / "city-check.json").write_text(
        json.dumps({"results": [{"body": "City Council", "ok": True}]}), encoding="utf-8"
    )
    (city / "roster.json").write_text(
        json.dumps({"body": "City Council", "members": [{"name": "Ada"}]}), encoding="utf-8"
    )
    (city / "weekly.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
                "days": 14,
                "diarize": False,
            }
        ),
        encoding="utf-8",
    )
    station = app.state.hub.load("charlie").station
    run = station.run(station.run_now_with([["true"]], "test"))
    run.status = "failed"
    run.log = "> the model failed\nno audio came back"

    page = client.get("/setup/done")
    assert "no audio came back" in page.text
    assert "1 thing needs work." in page.text
    assert 'href="/setup/test"' in page.text


def test_schedule_off_needs_work_and_links_to_every_week(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    (cities / "alpha" / "city" / "weekly.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
                "days": 14,
                "diarize": False,
            }
        ),
        encoding="utf-8",
    )

    page = client.get("/setup/done")
    assert "The weekly schedule is off." in page.text
    assert 'href="/setup/weekly"' in page.text


def test_a_missing_windows_task_needs_work_and_links_to_every_week(done_hub_app):
    app, cities = done_hub_app
    app.state.hub.task_runner, _calls = task_runner(installed=False)
    client = client_for(app)
    prepare_everything_ok(app, cities, client)

    page = client.get("/setup/done")
    assert "The Windows task is not set up." in page.text
    assert "1 thing needs work." in page.text
    assert 'href="/setup/weekly"' in page.text


def test_a_chosen_website_with_nothing_published_is_ok_with_the_note(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    (cities / "alpha" / "site.json").write_text(
        json.dumps({"target": "cloudflare", "base_url": "https://alpha.example.org"}),
        encoding="utf-8",
    )

    page = client.get("/setup/done")
    assert "The website is built after you publish a brief on the desk." in page.text
    assert "Everything works." in page.text  # OK-with-note, not Needs work
    assert 'href="/c/alpha/"' in page.text


def test_a_chosen_website_with_a_published_item_but_no_build_needs_work(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    (cities / "alpha" / "site.json").write_text(
        json.dumps({"target": "cloudflare", "base_url": "https://alpha.example.org"}),
        encoding="utf-8",
    )
    store = app.state.hub.desks["alpha"].store
    store.publish(store.list_items("review")[0].id)

    page = client.get("/setup/done")
    assert "The website is chosen but has not been built yet." in page.text
    assert "1 thing needs work." in page.text
    assert 'href="/setup/website"' in page.text


def test_a_folder_website_whose_web_root_has_no_index_needs_work(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    web_root = cities / "alpha-web"
    web_root.mkdir()
    (cities / "alpha" / "site.json").write_text(
        json.dumps(
            {
                "target": "folder",
                "base_url": "https://alpha.example.org",
                "media_base_url": "https://alpha.example.org/media",
                "out": str(web_root),
                "media_out": str(web_root / "media"),
            }
        ),
        encoding="utf-8",
    )
    store = app.state.hub.desks["alpha"].store
    store.publish(store.list_items("review")[0].id)
    # The build output exists, so the only problem is the empty web root.
    out = cities / "alpha" / "site" / "out"
    out.mkdir(parents=True)
    (out / "index.html").write_text("<html></html>", encoding="utf-8")

    page = client.get("/setup/done")
    assert "has no index.html" in page.text
    assert "1 thing needs work." in page.text
    assert 'href="/setup/website"' in page.text


def test_no_website_is_ok(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    (cities / "alpha" / "site.json").write_text(json.dumps({"target": "none"}), encoding="utf-8")

    page = client.get("/setup/done")
    assert "No website (your choice)." in page.text
    assert "Everything works." in page.text


def test_the_home_page_opens_the_wizard_when_there_is_no_city(tmp_path, monkeypatch):
    monkeypatch.setattr(web_module, "Station", FakeStation)
    app = make_hub_app(tmp_path / "hub.db", tmp_path / "empty-cities")
    client = client_for(app)
    reply = client.get("/", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == "/setup"


def test_the_home_page_stays_the_city_list_with_one_city(hub_app):
    app, _cities = hub_app
    client = client_for(app)
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/setup"' in page.text  # the Set up link, not a redirect


def test_sign_up_still_comes_first_before_the_wizard(tmp_path, monkeypatch):
    monkeypatch.setattr(web_module, "Station", FakeStation)
    app = make_hub_app(tmp_path / "hub.db", tmp_path / "empty-cities")
    anon = TestClient(app, base_url="https://127.0.0.1")
    reply = anon.get("/", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == "/sign-in"


def test_the_everything_works_page_needs_a_sign_in(hub_app):
    app, _cities = hub_app
    anon = TestClient(app, base_url="https://127.0.0.1")
    reply = anon.get("/setup/done", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == "/sign-in"


# ----- the Rosters row counts every body the sources cover ------------------------------------


def _two_body_sources(cities, slug="alpha"):
    (cities / slug / "city" / "sources.json").write_text(
        json.dumps(
            {
                "format": 1,
                "sources": [
                    {
                        "kind": "webpage",
                        "url": "https://example.org/meetings",
                        "bodies": ["City Council", "Planning Commission"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_a_body_with_no_roster_makes_the_rosters_row_needs_work(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    _two_body_sources(cities)

    page = client.get("/setup/done")
    assert "No members for: Planning Commission." in page.text
    assert "1 thing needs work." in page.text
    assert 'href="/setup/rosters"' in page.text


def test_every_body_with_a_roster_makes_the_rosters_row_ok_again(done_hub_app):
    app, cities = done_hub_app
    client = client_for(app)
    prepare_everything_ok(app, cities, client)
    _two_body_sources(cities)
    rosters = cities / "alpha" / "city" / "rosters"
    rosters.mkdir()
    (rosters / "planning-commission.json").write_text(
        json.dumps({"body": "Planning Commission", "members": [{"name": "Ada"}]}),
        encoding="utf-8",
    )

    page = client.get("/setup/done")
    assert "No members for" not in page.text
    assert "Everything works." in page.text


# ----- the Models and voices step's wording ---------------------------------------------------


def test_copy_settings_names_the_cities_it_talks_about(tmp_path):
    source = tmp_path / "cities" / "alpha" / "city"
    same = tmp_path / "cities" / "bravo" / "city"
    missing = tmp_path / "cities" / "charlie" / "city"
    for folder in (source, same, missing):
        folder.mkdir(parents=True)
    (source / "models.json").write_text("models", encoding="utf-8")
    (source / "voices.json").write_text("voices", encoding="utf-8")
    (same / "models.json").write_text("models", encoding="utf-8")

    lines = setup_wizard.copy_settings(
        source,
        [same, missing],
        names={source: "Alpha City", same: "Bravo Town", missing: "Charlie City"},
    )

    assert "Bravo Town: copied voices.json." in lines
    assert "Charlie City: copied models.json, voices.json." in lines
    assert not any("bravo:" in line or "charlie:" in line for line in lines)


def test_copy_settings_says_the_first_city_uses_the_built_in_models(tmp_path):
    source = tmp_path / "cities" / "alpha" / "city"
    keeps = tmp_path / "cities" / "bravo" / "city"
    plain = tmp_path / "cities" / "charlie" / "city"
    for folder in (source, keeps, plain):
        folder.mkdir(parents=True)
    (keeps / "models.json").write_text("own", encoding="utf-8")

    lines = setup_wizard.copy_settings(
        source,
        [keeps, plain],
        names={source: "Alpha City", keeps: "Bravo Town", plain: "Charlie City"},
    )

    assert "Alpha City uses the built-in models; there is no models.json to copy." in lines
    assert "Bravo Town keeps its own models.json." in lines
    assert not any("Charlie City keeps" in line for line in lines)


def test_voice_summary_with_no_voices_json_names_no_engine(tmp_path):
    summary = setup_wizard.voice_summary(tmp_path)
    assert summary["engine"] == "Not chosen yet"
    assert summary["fallback"] == "—"
    assert summary["voices"] == {}
    assert summary["note"] == "Open the Voices page and press Save."


def test_voice_summary_shows_an_empty_saved_fallback_as_none(tmp_path):
    (tmp_path / "voices.json").write_text(
        json.dumps({"engine": "gemini", "fallback_engine": "", "audio": {}}), encoding="utf-8"
    )
    summary = setup_wizard.voice_summary(tmp_path)
    assert summary["engine"] == "Gemini"
    assert summary["fallback"] == "None (no retry)"
    assert summary["note"] == ""
