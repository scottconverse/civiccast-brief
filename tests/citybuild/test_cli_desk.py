"""`city find`, `city check` and the desk's Find screen, all against saved pages."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from civiccast_brief.citybuild import browser, cli, members
from civiccast_brief.citybuild.fetch import Page
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app
from civiccast_brief.sources import add_source, load_sources, make_source

from .conftest import (
    LONGMONT_HOME,
    SVCM,
    SVCM_NEWEST,
    TPCA_ROOT,
    add_primegov,
    add_tpca_drive,
    gdoc_export,
)
from .test_search import longmont


def find_args(**kw) -> Namespace:
    base = {
        "name": "Longmont",
        "state": "CO",
        "website": LONGMONT_HOME,
        "also": [],
        "out": None,
        "dry_run": False,
        "verbose": False,
    }
    return Namespace(**{**base, **kw})


def test_also_lines_need_a_bar():
    assert cli.parse_also(["Board | https://x.test/ "]) == [("Board", "https://x.test/")]
    with pytest.raises(ValueError, match="Body name"):
        cli.parse_also(["Board https://x.test/"])


def test_find_prints_and_writes_only_with_out(web, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(members, "PAUSE_S", 0)
    longmont(web)
    assert cli._find(find_args(), web.fetcher()) == 0
    out = capsys.readouterr().out
    assert "Sources the program can read (2):" in out
    assert "no model" in out
    assert "not written: give --out FOLDER" in out
    assert not list(tmp_path.iterdir())
    folder = tmp_path / "longmont"
    also = [f"St. Vrain Community Montessori Board | {SVCM}"]
    assert cli._find(find_args(out=folder, also=also), web.fetcher()) == 0
    out = capsys.readouterr().out
    assert f"wrote {folder / 'city.json'}" in out
    assert "filled in later: the City Council members: nothing to type, the weekly run fills" in out
    assert "contact_email" not in out  # the podcast address is the publisher's
    assert [s.kind for s in load_sources(folder)] == ["primegov", "youtube", "webpage"]


def test_find_refuses_a_bad_also_line(web, capsys):
    assert cli._find(find_args(also=["no bar here"]), web.fetcher()) == 2
    assert "write it as" in capsys.readouterr().err


def test_check_reads_each_body_and_saves_the_proof(web, tmp_path, capsys):
    add_tpca_drive(web)
    web.add(gdoc_export(SVCM_NEWEST), "Board Meeting Minutes\nSeptember 10, 2026", "text/plain")
    web.fixture(SVCM, "svcm-board-meetings.html")
    add_source(tmp_path, make_source("gdrive", {"folder_id": TPCA_ROOT, "bodies": ["TPCA Board"]}))
    add_source(tmp_path, make_source("webpage", {"url": SVCM, "bodies": ["SVCM Board"]}))
    proofs = cli.check_city(tmp_path, fetcher=web.fetcher(), today=date(2026, 11, 10))
    assert [(p.body, p.ok) for p in proofs] == [("TPCA Board", True), ("SVCM Board", True)]
    only = cli.check_city(
        tmp_path, bodies=["svcm board"], fetcher=web.fetcher(), today=date(2026, 11, 10)
    )
    assert [p.body for p in only] == ["SVCM Board"]
    args = Namespace(city=tmp_path, body=None, captions=False)
    assert cli._check(args, web.fetcher()) == 0
    out = capsys.readouterr().out
    assert "READ      TPCA Board (gdrive," in out
    assert "2 of 2 body sources read" in out
    saved = json.loads((tmp_path / cli.PROOF_FILE).read_text(encoding="utf-8"))
    assert [r["ok"] for r in saved["results"]] == [True, True]


def test_check_fails_when_a_body_is_not_read(web, tmp_path, capsys):
    add_source(tmp_path, make_source("webpage", {"url": SVCM, "bodies": ["SVCM Board"]}))
    assert cli._check(Namespace(city=tmp_path, body=None, captions=False), web.fetcher()) == 1
    out = capsys.readouterr().out
    assert "NOT READ  SVCM Board" in out and "0 of 1 body sources read" in out


def test_the_city_command_is_wired_into_the_main_parser(capsys):
    from civiccast_brief.__main__ import main

    with pytest.raises(SystemExit) as stop:
        main(["city", "check", "--help"])
    assert stop.value.code == 0
    assert "--captions" in capsys.readouterr().out


# ----- the desk ---------------------------------------------------------------------------------


@pytest.fixture
def desk(web, tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    (city / "city.json").write_text('{"name": "Longmont"}', encoding="utf-8")
    with ReviewStore(tmp_path / "d.db") as store:
        app = make_desk_app(store, city_dir=city)
        app.state.desk.make_fetcher = web.fetcher
        yield TestClient(app, base_url="http://127.0.0.1"), city


def test_the_find_screen_searches_then_adds_only_the_checked_sources(web, desk, monkeypatch):
    monkeypatch.setattr(members, "PAUSE_S", 0)
    client, city = desk
    longmont(web)
    t = client.app.state.desk.token
    page = client.get("/sources/find")
    assert page.status_code == 200
    assert 'value="Longmont"' in page.text  # the name comes from city.json
    assert 'data-busy="Searching' in page.text
    assert 'href="/sources/find"' in client.get("/sources").text
    found = client.post(
        "/sources/find",
        data={
            "token": t,
            "name": "Longmont",
            "state": "CO",
            "website": LONGMONT_HOME,
            "also": "\nTPCA Board | https://www.twinpeaksclassical.org/page/governance\n",
        },
    )
    assert found.status_code == 200
    assert "Sources the program can read" in found.text
    assert f'value="gdrive:{TPCA_ROOT}" checked' in found.text
    assert 'data-busy="Adding the sources' in found.text
    assert load_sources(city) == []  # nothing is saved by the search
    search = client.app.state.desk.searches
    (search_id,) = search
    saved = client.post(
        "/sources/find/save",
        data={
            "token": t,
            "search": search_id,
            "chosen": [f"gdrive:{TPCA_ROOT}", "primegov:longmont.primegov.com"],
        },
    )
    assert saved.status_code == 200
    assert "Added 2 sources: PrimeGov portal (" in saved.text
    assert "Google Drive folder (1 body)" in saved.text
    assert "Kept city.json (already there)." in saved.text
    assert "Wrote roster.json with no members yet: the weekly run fills them in" in saved.text
    assert "Filled in during the weekly run: the City Council members" in saved.text
    assert "saved the search to" not in saved.text  # the notice names files, not paths
    assert "added https://longmont.primegov.com" not in saved.text
    assert sorted(s.kind for s in load_sources(city)) == ["gdrive", "primegov"]
    assert json.loads((city / "city.json").read_text(encoding="utf-8")) == {"name": "Longmont"}
    assert (city / "roster.json").exists()


def test_the_find_screen_names_what_is_missing(web, desk):
    client, _city = desk
    t = client.app.state.desk.token
    no_state = client.post("/sources/find", data={"token": t, "name": "Longmont", "state": ""})
    assert no_state.status_code == 400 and "Enter the state" in no_state.text
    bad = client.post(
        "/sources/find", data={"token": t, "name": "Longmont", "state": "CO", "also": "x"}
    )
    assert bad.status_code == 400 and "The line" in bad.text
    stale = client.post("/sources/find/save", data={"token": t, "search": "gone", "chosen": "x"})
    assert stale.status_code == 400 and "Search again" in stale.text
    assert client.post("/sources/find", data={"token": "wrong"}).status_code == 403
    assert client.post("/sources/find/save", data={"token": "wrong"}).status_code == 403


def test_adding_with_nothing_checked_saves_nothing(web, desk):
    client, city = desk
    web.add("https://none.test/", "<html>nothing</html>")
    web.fixture(SVCM, "svcm-board-meetings.html")
    t = client.app.state.desk.token
    client.post(
        "/sources/find",
        data={
            "token": t,
            "name": "Nowhere",
            "state": "CO",
            "website": "https://none.test/",
            "also": f"SVCM Board | {SVCM}",
        },
    )
    (search_id,) = client.app.state.desk.searches
    empty = client.post("/sources/find/save", data={"token": t, "search": search_id})
    assert empty.status_code == 400 and "check at least one source" in empty.text
    assert load_sources(city) == []


def test_the_find_screen_reads_a_site_that_answers_403_in_chrome(web, desk, monkeypatch):
    """lovgov.org answers a plain client 403; the desk opens it in Chrome (2026-11-12)."""
    client, _city = desk
    home = "https://www.lovgov.org"
    web.add(home, "<html>Access Denied</html>", status=403)
    add_primegov(web, "https://loveland.primegov.com")
    opened: list[str] = []
    closed: list[bool] = []

    def open_page(url: str) -> Page:
        opened.append(url)
        html = '<html><a href="https://loveland.primegov.com/">Agendas</a></html>'
        return Page(url=url, status=200, content=html.encode(), content_type="text/html")

    monkeypatch.setattr(browser, "open_page", open_page)
    monkeypatch.setattr(browser, "close_shared", lambda: closed.append(True))
    found = client.post(
        "/sources/find",
        data={
            "token": client.app.state.desk.token,
            "name": "Loveland",
            "state": "CO",
            "website": home,
        },
    )
    assert found.status_code == 200
    assert opened == [home]
    assert closed
    assert "primegov:loveland.primegov.com" in found.text


def test_the_desk_closes_chrome_in_the_thread_that_opened_it(web, desk, monkeypatch):
    """Playwright objects belong to the thread that made them. Closing Chrome on the server's
    loop thread killed the desk after every Find search (newcomer test, 2026-11-12)."""
    import threading

    from civiccast_brief.citybuild import build

    client, _city = desk
    home = "https://www.lovgov.org"
    web.add(home, "<html>Access Denied</html>", status=403)
    add_primegov(web, "https://loveland.primegov.com")
    threads: dict[str, list[int]] = {"open": [], "close": [], "build": []}

    def open_page(url: str) -> Page:
        threads["open"].append(threading.get_ident())
        html = '<html><a href="https://loveland.primegov.com/">Agendas</a></html>'
        return Page(url=url, status=200, content=html.encode(), content_type="text/html")

    real_build = build.build_city

    def build_city(*args, **kw):
        threads["build"].append(threading.get_ident())
        return real_build(*args, **kw)

    monkeypatch.setattr(browser, "open_page", open_page)
    monkeypatch.setattr(
        browser, "close_shared", lambda: threads["close"].append(threading.get_ident())
    )
    monkeypatch.setattr(build, "build_city", build_city)
    t = client.app.state.desk.token
    found = client.post(
        "/sources/find",
        data={"token": t, "name": "Loveland", "state": "CO", "website": home},
    )
    assert found.status_code == 200
    assert threads["open"] and threads["close"] == threads["open"][:1]
    (search_id,) = client.app.state.desk.searches
    saved = client.post(
        "/sources/find/save",
        data={"token": t, "search": search_id, "chosen": "primegov:loveland.primegov.com"},
    )
    assert saved.status_code == 303 or saved.status_code == 200
    assert threads["build"] and threads["close"][1:] == threads["build"]


def test_a_youtube_channel_find_did_not_offer_can_be_added_anyway(web, desk):
    from .conftest import CHANNEL, feed

    client, city = desk
    t = client.app.state.desk.token
    web.add(
        "https://city.test/",
        f'<html><a href="https://www.youtube.com/channel/{CHANNEL}">Our channel</a></html>',
    )
    web.add(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}",
        feed(("aaaaaaaaaaa", "Parade highlights", "2026-10-29T03:00:00+00:00")),
    )
    found = client.post(
        "/sources/find",
        data={"token": t, "name": "Longmont", "state": "CO", "website": "https://city.test/"},
    )
    assert "names a body" in found.text and "Add anyway" in found.text
    assert 'data-busy="Adding the channel' in found.text
    (search_id,) = client.app.state.desk.searches
    assert load_sources(city) == []
    none = client.post(
        "/sources/find/add-anyway",
        data={"token": t, "search": search_id, "index": "0", "bodies": " "},
    )
    assert none.status_code == 400 and "Not added" in none.text and "Add anyway" in none.text
    assert load_sources(city) == []
    added = client.post(
        "/sources/find/add-anyway",
        data={"token": t, "search": search_id, "index": "0", "bodies": "City Council"},
    )
    assert added.status_code == 200
    assert "Added 1 source: YouTube channel" in added.text
    (source,) = load_sources(city)
    assert (source.kind, source.channel_id, source.bodies) == (
        "youtube",
        CHANNEL,
        ("City Council",),
    )
    stale = client.post(
        "/sources/find/add-anyway",
        data={"token": t, "search": "gone", "index": "0", "bodies": "x"},
    )
    assert stale.status_code == 400 and "Search again" in stale.text
    assert client.post("/sources/find/add-anyway", data={"token": "wrong"}).status_code == 403
