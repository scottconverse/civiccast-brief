"""The city search against the saved Longmont pages, and the stubs it writes."""

from __future__ import annotations

import json
from datetime import date

from civiccast_brief.citybuild.build import REPORT_FILE, build_city
from civiccast_brief.citybuild.search import PROBED, guesses, search_city
from civiccast_brief.sources import load_sources, make_source

from .conftest import (
    BOULDER_COUNTY,
    CHANNEL,
    ERIE,
    FLAGSTAFF,
    LAFAYETTE,
    LONGMONT_HOME,
    LYONS,
    PRIMEGOV,
    SVCM,
    SVVSD,
    TPCA,
    TPCA_ROOT,
    add_boulder_county,
    add_erie,
    add_primegov,
    add_tpca_drive,
    data_api,
    feed,
)

TODAY = date(2026, 11, 10)
EXTRA = [
    ("Board of Education", SVVSD),
    ("Flagstaff Academy Board of Directors", FLAGSTAFF),
    ("St. Vrain Community Montessori Board", SVCM),
    ("Twin Peaks Classical Academy Board of Directors", TPCA),
]


def longmont(web) -> None:
    web.fixture(LONGMONT_HOME, "longmont-home.html")
    add_primegov(web)
    web.add(
        "https://www.youtube.com/cityoflongmont",
        f'<html><link rel="canonical" href="https://www.youtube.com/channel/{CHANNEL}"></html>',
    )
    web.add(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}",
        feed(
            ("aaaaaaaaaaa", "City Council Regular Session 10/28/2026", "2026-10-29T03:00:00+00:00"),
            (
                "bbbbbbbbbbb",
                "Planning and Zoning Commission 11/5/2026",
                "2026-11-06T03:00:00+00:00",
            ),
            ("ccccccccccc", "Holiday lights downtown", "2026-11-07T03:00:00+00:00"),
        ),
        "application/atom+xml",
    )
    web.fixture(SVVSD, "svvsd-2026-board-meetings.html")
    web.fixture(FLAGSTAFF, "flagstaff-board-documents.html")
    web.fixture(SVCM, "svcm-board-meetings.html")
    web.fixture(TPCA, "tpca-governance.html")
    add_tpca_drive(web)


def by_kind(found) -> dict[str, list]:
    out: dict[str, list] = {}
    for c in found.candidates:
        out.setdefault(c.source.kind, []).append(c.source)
    return out


def test_longmont_is_found_from_its_home_page_and_the_given_pages(web):
    longmont(web)
    found = search_city(
        "Longmont", "CO", website=LONGMONT_HOME, extra=EXTRA, fetcher=web.fetcher(), today=TODAY
    )
    kinds = by_kind(found)
    (portal,) = kinds["primegov"]
    assert portal.url == PRIMEGOV
    assert len(portal.bodies) == 29
    assert {"City Council", "Planning and Zoning Commission", "Water Board"} <= set(portal.bodies)
    (channel,) = kinds["youtube"]
    assert channel.channel_id == CHANNEL
    assert channel.bodies == ("City Council", "Planning and Zoning Commission")
    pages = {s.bodies[0]: s for s in kinds["webpage"]}
    assert pages["Board of Education"].follow == "/boardmeetings/"
    assert pages["Board of Education"].url == SVVSD
    assert pages["Flagstaff Academy Board of Directors"].follow == ""
    assert pages["St. Vrain Community Montessori Board"].url == SVCM
    (folder,) = kinds["gdrive"]
    assert folder.folder_id == TPCA_ROOT
    assert folder.bodies == ("Twin Peaks Classical Academy Board of Directors",)
    (how,) = [c.how for c in found.candidates if c.source.kind == "gdrive"]
    assert '("26-27 BOD Agendas, Minutes, Committees")' in how
    assert ", 1 entry" in how, how
    # No model is asked anything: every request is a plain GET to a page or a public list.
    assert all(url.startswith("https://") for url in web.asked)
    assert not any("anthropic" in url or "openai" in url for url in web.asked)
    text = "\n".join(found.report())
    assert "Sources the program can read (6):" in text


def test_a_city_with_no_platform_link_is_probed_by_name(web):
    web.add("https://www.smalltown.test/", "<html><title>Smalltown</title>Smalltown</html>")
    add_primegov(web, "https://smalltown.primegov.com")
    web.add(
        "https://webapi.legistar.com/v1/smalltown/bodies",
        json.dumps([{"BodyName": "City Council"}, {"BodyName": "Zoning Board"}]),
        "application/json",
    )
    found = search_city(
        "Smalltown", "CO", website="https://www.smalltown.test/", fetcher=web.fetcher(), today=TODAY
    )
    (portal,) = [c for c in found.candidates if c.source.kind == "primegov"]
    assert "check that it is this city's portal" in portal.how
    assert [(u.platform, u.note) for u in found.unread] == [
        (
            "Legistar",
            f"{PROBED}; 2 bodies: City Council, Zoning Board",
        )
    ]


def test_the_website_is_guessed_from_the_name_and_state(web):
    assert guesses("Longmont", "Colorado")[0] == "https://www.longmontcolorado.gov/"
    assert "https://longmontco.gov/" in guesses("Longmont", "CO")
    web.add("https://www.longmontcolorado.gov/", "<html>City of Longmont</html>")
    found = search_city("Longmont", "CO", fetcher=web.fetcher(), today=TODAY)
    assert found.website == "https://www.longmontcolorado.gov/"
    assert "website guessed from the name" in found.notes[0]


def test_a_city_with_no_website_says_so(web):
    found = search_city("Nowhere", "CO", fetcher=web.fetcher(), today=TODAY)
    assert found.candidates == []
    assert "give it with --website" in found.notes[0]


def test_a_given_page_with_nothing_to_read_is_named(web):
    web.add("https://club.test/", "<html>No meetings here</html>")
    found = search_city(
        "Nowhere",
        "CO",
        website="https://club.test/",
        extra=[("Club Board", "https://club.test/")],
        fetcher=web.fetcher(),
        today=TODAY,
    )
    assert any("has no dated documents, Drive folders or meeting pages" in n for n in found.notes)


def test_a_given_platform_with_no_reader_is_listed_not_saved(web):
    found = search_city(
        "Nowhere",
        "CO",
        website="https://none.test/",
        extra=[("Board", "https://go.boarddocs.com/co/bvsd/Board.nsf/Public")],
        fetcher=web.fetcher(),
        today=TODAY,
    )
    assert found.candidates == []
    assert [(u.platform, u.note) for u in found.unread] == [("BoardDocs", "for Board")]


def test_build_writes_stubs_once_and_merges_sources(web, tmp_path):
    longmont(web)
    found = search_city(
        "Longmont", "CO", website=LONGMONT_HOME, extra=EXTRA, fetcher=web.fetcher(), today=TODAY
    )
    # The editor already saved the channel for the council only.
    folder = tmp_path / "longmont"
    folder.mkdir()
    from civiccast_brief.sources import add_source

    add_source(folder, make_source("youtube", {"channel_id": CHANNEL, "bodies": ["City Council"]}))
    built = build_city(folder, found)
    city = json.loads((folder / "city.json").read_text(encoding="utf-8"))
    assert city["name"] == "Longmont"
    assert city["slug"] == "longmont"
    roster = json.loads((folder / "roster.json").read_text(encoding="utf-8"))
    assert "City Council" in json.dumps(roster)
    assert any("contact_email" in line for line in built.to_fill)
    saved = load_sources(folder)
    assert len(saved) == 6
    (channel,) = [s for s in saved if s.kind == "youtube"]
    assert channel.bodies == ("City Council", "Planning and Zoning Commission")
    assert any("now also covers Planning and Zoning Commission" in line for line in built.lines)
    report = json.loads((folder / REPORT_FILE).read_text(encoding="utf-8"))
    assert len(report["sources"]) == 6
    # A second run changes nothing and keeps the editor's city.json.
    (folder / "city.json").write_text('{"name": "Longmont", "edited": true}', encoding="utf-8")
    again = build_city(folder, found)
    assert "sources: nothing new" in again.lines
    assert json.loads((folder / "city.json").read_text(encoding="utf-8"))["edited"] is True


def test_build_saves_only_the_chosen_sources(web, tmp_path):
    longmont(web)
    found = search_city(
        "Longmont", "CO", website=LONGMONT_HOME, extra=EXTRA, fetcher=web.fetcher(), today=TODAY
    )
    build_city(tmp_path, found, chosen=[f"gdrive:{TPCA_ROOT}"])
    assert [s.id for s in load_sources(tmp_path)] == [f"gdrive:{TPCA_ROOT}"]


def test_a_channel_whose_feed_answers_404_is_found_through_the_data_api(web, monkeypatch):
    """Longmont's feed answered 404 on 2026-11-10; the saved key still lists the channel."""
    from civiccast_brief.record import credentials

    monkeypatch.setattr(credentials, "read_credential", lambda target: "fake-key-not-real")
    longmont(web)
    web.add(f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}", b"", status=404)
    api = data_api(
        ("aaaaaaaaaaa", "City Council Regular Session 10/28/2026", "2026-10-29T03:00:00Z"),
        ("bbbbbbbbbbb", "Planning and Zoning Commission 11/5/2026", "2026-11-06T03:00:00Z"),
    )
    pages = web.handle
    web.handle = lambda r: api(r) if r.url.host == "www.googleapis.com" else pages(r)

    found = search_city(
        "Longmont", "CO", website=LONGMONT_HOME, extra=EXTRA, fetcher=web.fetcher(), today=TODAY
    )

    (channel,) = by_kind(found)["youtube"]
    assert channel.channel_id == CHANNEL
    assert channel.bodies == ("City Council", "Planning and Zoning Commission")
    assert not [u for u in found.unread if u.platform == "YouTube"]


# ----- CivicClerk, eScribe and CivicPlus ---------------------------------------------------------


def test_a_linked_civicclerk_portal_is_saved_with_every_body(web):
    web.add(
        "https://www.erieco.test/",
        '<html><a href="https://erieco.portal.civicclerk.com/">Agendas and minutes</a></html>',
    )
    add_erie(web, TODAY)
    found = search_city(
        "Erie", "CO", website="https://www.erieco.test/", fetcher=web.fetcher(), today=TODAY
    )
    (portal,) = [c for c in found.candidates if c.source.kind == "civicclerk"]
    assert portal.source.id == f"civicclerk:{ERIE}"
    assert len(portal.source.bodies) == 10
    assert portal.source.bodies[0] == "Town Council"
    assert "General" not in portal.source.bodies
    assert portal.how == "linked from the city site; the portal lists 10 bodies"
    assert not found.unread


def test_given_escribe_and_civicplus_pages_are_saved_for_the_given_body(web):
    add_boulder_county(web)
    web.fixture(f"https://{LYONS}/AgendaCenter", "civicplus-lyons-agendacenter.html")
    web.fixture(f"https://{LAFAYETTE}/Archive.aspx", "civicplus-lafayette-archive.html")
    found = search_city(
        "Nowhere",
        "CO",
        website="https://none.test/",
        extra=[
            ("Board of County Commissioners", "https://pub-bouldercounty.escribemeetings.com/"),
            ("Board of Trustees", f"https://{LYONS}/AgendaCenter"),
            ("City Council", f"https://{LAFAYETTE}/Archive.aspx"),
        ],
        fetcher=web.fetcher(),
        today=TODAY,
    )
    got = {c.source.id: (c.source.bodies, c.how) for c in found.candidates}
    assert got == {
        f"escribe:{BOULDER_COUNTY}": (
            ("Board of County Commissioners",),
            "given; its calendar's last 120 days show 3 bodies",
        ),
        f"civicplus:{LYONS}": (("Board of Trustees",), "given; the Agenda Center list 2 bodies"),
        f"civicplus:{LAFAYETTE}": (("City Council",), "given; the Archive Center list 2 bodies"),
    }


def test_a_portal_whose_body_list_does_not_answer_is_named(web):
    found = search_city(
        "Nowhere",
        "CO",
        website="https://none.test/",
        extra=[("Board", "https://erieco.portal.civicclerk.com/")],
        fetcher=web.fetcher(),
        today=TODAY,
    )
    assert found.candidates == []
    ((platform, note),) = [(u.platform, u.note) for u in found.unread]
    assert platform == "CivicClerk"
    assert note.startswith("its body list did not answer: ")


def test_archive_lists_of_other_documents_are_not_taken_for_bodies(web):
    # The Archive Center markup as Lafayette, Erie and Lyons serve it, one list per module.
    def module(number: int, label: str, item: str) -> str:
        return (
            f'<label for="amidDDN{number}">{label}</label><br>'
            f'<select id="amidDDN{number}"><option value="1_1_0_{number}00">{item} </option></select>'
        )

    web.add(
        "https://www.town.test/Archive.aspx",
        "<html>"
        + module(1, "Mayor's Message:", "November 1, 2026")
        + module(2, "Board of Trustees Public Hearing Notices:", "November 10, 2026")
        + module(3, "Local Liquor Licensing Authority:", "November 3, 2026 Agenda Packet")
        + module(4, "Town Council Agendas:", "November 4, 2026")
        + module(5, "Town Council Minutes:", "October 21, 2026")
        + "</html>",
    )
    web.add("https://www.town.test/", '<html><a href="/Archive.aspx">Archive Center</a></html>')
    found = search_city(
        "Town", "CO", website="https://www.town.test/", fetcher=web.fetcher(), today=TODAY
    )
    (site,) = found.candidates
    assert site.source.bodies == ("Local Liquor Licensing Authority", "Town Council")
