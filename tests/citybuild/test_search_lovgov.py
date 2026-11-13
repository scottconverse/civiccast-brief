"""Find on Loveland's own site (lovgov.org): the Cablecast archive linked from the council page.

The two pages are trimmed copies of the live pages saved on 2026-11-12 (the links are the real
ones, in their order). Nothing reaches the network.
"""

from __future__ import annotations

import json
from datetime import date

from civiccast_brief.citybuild.search import search_city
from civiccast_brief.meeting import cablecast

from .conftest import HERE

TODAY = date(2026, 11, 12)
HOME = "https://www.lovgov.org/"
MEETINGS = "https://www.lovgov.org/city-government/city-council/city-council-meetings"
HOST = "reflect-cityofloveland-co.cablecast.tv"
SHOWS = HERE.parent.parent / "meeting" / "fixtures" / "cablecast" / "shows.json"


def _shows(monkeypatch) -> None:
    data = json.loads(SHOWS.read_text(encoding="utf-8"))
    monkeypatch.setattr(cablecast, "list_shows", lambda host: cablecast.parse_shows(data, host))


def _site(web, home_html: str | None = None) -> None:
    if home_html is None:
        web.fixture(HOME, "lovgov-home.html")
    else:
        web.add(HOME, home_html)
    web.fixture(MEETINGS, "lovgov-council-meetings.html")


def test_find_offers_the_cablecast_archive_linked_from_the_council_meetings_page(web, monkeypatch):
    _shows(monkeypatch)
    _site(web)
    found = search_city("Loveland", "CO", website=HOME, fetcher=web.fetcher(), today=TODAY)
    (cable,) = [c for c in found.candidates if c.source.kind == "cablecast"]
    assert cable.source.host == HOST
    assert {"City Council", "Planning Commission"} <= set(cable.source.bodies)
    assert not [u for u in found.unread if u.platform == "Cablecast"]


def test_the_council_page_is_read_even_when_the_menu_lists_many_pages_before_it(web, monkeypatch):
    _shows(monkeypatch)
    rooms = "".join(
        f'<a href="https://www.lovgov.org/rooms/room-{n}">Board room {n}</a>' for n in range(9)
    )
    home = (HERE / "lovgov-home.html").read_text(encoding="utf-8")
    _site(web, home.replace("<body>", f"<body>{rooms}", 1))
    found = search_city("Loveland", "CO", website=HOME, fetcher=web.fetcher(), today=TODAY)
    assert MEETINGS in web.asked
    assert [c.source.kind for c in found.candidates].count("cablecast") == 1


def test_single_calendar_event_pages_are_not_read(web, monkeypatch):
    _shows(monkeypatch)
    events = "".join(
        f'<a href="https://www.lovgov.org/Home/Components/Calendar/Event/{n}/20">Planning Commission</a>'
        for n in range(9)
    )
    home = (HERE / "lovgov-home.html").read_text(encoding="utf-8")
    _site(web, home.replace("<body>", f"<body>{events}", 1))
    search_city("Loveland", "CO", website=HOME, fetcher=web.fetcher(), today=TODAY)
    assert MEETINGS in web.asked
    assert not [url for url in web.asked if "/Calendar/Event/" in url]


def test_a_home_page_with_no_links_is_said_not_passed_over_in_silence(web):
    web.add(HOME, "<html><head><title>City of Loveland</title></head><body>Loading</body></html>")
    found = search_city("Loveland", "CO", website=HOME, fetcher=web.fetcher(), today=TODAY)
    assert any("lists no links" in note for note in found.notes)
