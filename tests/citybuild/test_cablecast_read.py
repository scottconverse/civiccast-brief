"""``city check`` and ``city find`` for Cablecast, from the real shows saved for the capture."""

import json
from datetime import date
from pathlib import Path

from civiccast_brief.citybuild import detect, search
from civiccast_brief.citybuild.read import read_latest
from civiccast_brief.meeting import cablecast
from civiccast_brief.sources import make_source

HOST = "reflect-cityofloveland-co.cablecast.tv"
FIXTURES = Path(__file__).parent.parent / "meeting" / "fixtures" / "cablecast"
TODAY = date(2026, 11, 12)


def _shows():
    data = json.loads((FIXTURES / "shows.json").read_text(encoding="utf-8"))
    return cablecast.parse_shows(data, HOST)


def test_city_check_reads_the_newest_past_show_of_the_body(web, monkeypatch):
    monkeypatch.setattr(cablecast, "list_shows", lambda host: _shows())
    source = make_source("cablecast", {"host": HOST, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.kind == "cablecast" and proof.address == f"https://{HOST}"
    assert proof.meeting_date == date(2026, 11, 4)
    assert proof.title == "City Council Meeting November 4, 2026"
    assert proof.document == f"https://{HOST}/internetchannel/show/746"


def test_city_check_does_not_read_a_show_after_today(web, monkeypatch):
    monkeypatch.setattr(cablecast, "list_shows", lambda host: _shows())
    source = make_source("cablecast", {"host": HOST, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=date(2026, 10, 1))
    # The October 6 meeting is after that day: the newest one before it is January 27.
    assert proof.ok and proof.meeting_date == date(2026, 1, 27)


def test_city_check_names_the_body_when_no_show_matches(web, monkeypatch):
    monkeypatch.setattr(cablecast, "list_shows", lambda host: _shows())
    source = make_source("cablecast", {"host": HOST, "bodies": ["Library Board"]})
    proof = read_latest(source, "Library Board", web.fetcher(), today=TODAY)
    assert not proof.ok
    assert proof.problem == f"no show on {HOST} names 'Library Board' in its title"


def test_city_check_reports_a_listing_that_fails(web, monkeypatch):
    def broken(host):
        raise cablecast.CablecastError("could not fetch: HTTP Error 503")

    monkeypatch.setattr(cablecast, "list_shows", broken)
    source = make_source("cablecast", {"host": HOST, "bodies": ["City Council"]})
    proof = read_latest(source, "City Council", web.fetcher(), today=TODAY)
    assert not proof.ok and "HTTP Error 503" in proof.problem


def test_a_cablecast_address_is_a_known_readable_platform():
    found = detect.classify(f"https://{HOST}/internetchannel/")
    assert found is not None
    assert (found.platform, found.key, found.label) == (
        "cablecast",
        "reflect-cityofloveland-co",
        "Cablecast",
    )
    assert "cablecast" in detect.READABLE
    assert detect.classify("https://cablecast.tv.example.com/") is None


def test_city_find_notices_a_cablecast_link_on_a_city_page():
    page = f'<p>Watch <a href="https://{HOST}/internetchannel">council video</a></p>'
    items = search._platform_links(page, "https://www.cityofloveland.org/government")
    assert {(i.platform, i.url) for i in items} == {
        ("cablecast", f"https://{HOST}/internetchannel")
    }
    # The same address in a script's JSON, with escaped slashes, is found too.
    script = r'{"video":"https:\/\/' + HOST + r'\/internetchannel"}'
    assert [i.platform for i in search._platform_links(script, "https://x.gov/")] == ["cablecast"]


def test_city_check_finds_a_civicweb_style_body_name_in_cablecast_titles(web, monkeypatch):
    monkeypatch.setattr(cablecast, "list_shows", lambda host: _shows())
    source = make_source("cablecast", {"host": HOST, "bodies": ["LOVELAND CITY COUNCIL MEETING"]})
    body = "LOVELAND CITY COUNCIL MEETING"
    bare = read_latest(source, body, web.fetcher(), today=TODAY)
    assert not bare.ok and "names 'LOVELAND CITY COUNCIL MEETING'" in bare.problem
    proof = read_latest(source, body, web.fetcher(), today=TODAY, city="Loveland")
    assert proof.ok, proof.problem
    assert proof.title == "City Council Meeting November 4, 2026"


def test_city_check_passes_the_name_in_city_json_to_the_reader(web, monkeypatch, tmp_path):
    import json

    from civiccast_brief.citybuild.cli import check_city

    monkeypatch.setattr(cablecast, "list_shows", lambda host: _shows())
    (tmp_path / "city.json").write_text(json.dumps({"name": "Loveland"}), encoding="utf-8")
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {"kind": "cablecast", "host": HOST, "bodies": ["LOVELAND CITY COUNCIL MEETING"]}
                ]
            }
        ),
        encoding="utf-8",
    )
    (proof,) = check_city(tmp_path, fetcher=web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem


def test_title_body_names_drop_the_date_and_count_the_shows():
    from civiccast_brief.citybuild.read import title_body_names

    titles = [s.name for s in _shows()]
    names = dict(title_body_names(titles))
    assert "City Council Meeting" in names and "Planning Commission" in names
    assert "Council Study Session" in names
    assert all(not any(ch.isdigit() for ch in name) for name in names)
    assert title_body_names(["City Council - Oct 20 2026", "City Council Oct 6 2026"]) == [
        ("City Council", 2)
    ]
