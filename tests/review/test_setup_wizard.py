"""The setup wizard: city lines, Find and Check jobs, rosters, copying, and the step gates."""

from __future__ import annotations

import json
import sys
import threading
import time

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
    assert "bravo: copied models.json." in page.text
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
    assert "Next" not in page.text or "Finish" in page.text


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
        ("post", "/setup/models-voices", {"action": "copy"}),
        ("post", "/setup/website", {"action": "save", "target": "none"}),
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
