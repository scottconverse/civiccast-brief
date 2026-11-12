"""Board rosters read from the towns' own board pages (saved from the live pages, 2026-11-11 and 12,
with email addresses and phone numbers cut out)."""

from __future__ import annotations

import argparse
import json

import pytest

from civiccast_brief.citybuild import boardsites, cli
from civiccast_brief.sources import make_source, merge_sources
from civiccast_brief.weekly.run import roster_for

from .conftest import HERE

BOARDS = HERE / "boards"


def _members(name: str) -> list[tuple[str, str]]:
    text = (BOARDS / name).read_text(encoding="utf-8")
    return [(m["name"], m["role"]) for m in boardsites.read_board_members(text)]


def test_the_fixtures_hold_no_email_address_or_phone_number():
    paths = list(BOARDS.glob("*.html"))
    assert len(paths) >= 14
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "@" not in text and "mailto:" not in text, path.name
        assert "303-" not in text and "(303)" not in text, path.name


def test_erie_table_gives_name_and_title_and_leaves_out_the_staff_liaison():
    assert _members("erie-planning-commission.html") == [
        ("Andrew Sawusch", "Chair"),
        ("Sherri Booth", "Member"),
        ("Alex Schatz", "Member"),
        ("Robert (Bob) Braudes", "Member"),  # the nickname is printed in the name
        ("Paul M. Leef", "Member"),
        ("Delaney Dreckman", "Vice Chair"),
        ("Ben Hemphill", "Member"),
    ]  # "Darryll Wolnik, Staff Liaison" is the last row


def test_a_vacant_seat_and_the_council_liaisons_are_not_members():
    assert _members("erie-historic-preservation-advisory-board.html") == [
        ("Kelsey Bibo", "Secretary"),
        ("DeAndrea Arndt", "Vice Chair"),
        ("Rachel Folger", "Member"),
        ("Cesar Jimenez", "Member"),
        ("Alex Wicks", "Member"),
    ]


def test_a_member_table_with_the_title_under_each_name():
    members = _members("erie-urban-renewal-authority.html")
    assert members[:2] == [("Andrew J. Moore", "Chairperson"), ("Anil Pesaramelli", "Commissioner")]
    assert len(members) == 8


def test_lafayette_table_has_the_title_in_its_own_column():
    members = _members("lafayette-planning-commission.html")
    assert members[:2] == [("Darcia Thomas", "Chair"), ("Dahvi Wilson", "Vice Chair")]
    assert len(members) == 7  # "Jacob Nitchals, Staff Liaison" is the eighth row


def test_lyons_list_has_the_liaisons_after_the_members():
    members = _members("lyons-planning-community-development-commission.html")
    assert members[0] == ("Neal Evers", "Vice Chair")
    assert len(members) == 7
    assert "Amy Schwartz" not in [name for name, _ in members]


def test_nederland_table_has_no_members_heading_and_a_planner_in_its_last_row():
    assert _members("nederland-planning-commission.html") == [
        ("Bill Renno", "Member"),
        ("Tom Mahowald", "Vice Chair"),
        ("Ben White", "Member"),
        ("Michael Dye", "Member"),
        ("Elizabeth Penziner", "Member"),
    ]  # Britt DeMinck, Community Planner, is staff


def test_boulder_prints_the_title_under_each_name_and_the_term_after_it():
    arts = _members("boulder-arts-commission.html")
    assert arts[5:] == [("Gaya Vinay", "Vice-Chair"), ("Yaelaed Whyel", "Chair")]
    assert len(arts) == 7


def test_boulder_names_the_board_in_each_title_and_prints_nicknames():
    parks = _members("boulder-parks-and-recreation-advisory-board.html")
    assert parks[:2] == [("Channing Benson", "Member"), ('Andrew "Bernie" Bernstein', "Chair")]
    beverage = _members("boulder-beverage-licensing-authority.html")
    assert ("Michael Carr", "Vice Chair") in beverage  # "... Board Member / Vice Chair"


def test_boulder_pronouns_and_notes_after_a_name_are_not_part_of_it():
    assert [n for n, _ in _members("boulder-human-relations-commission.html")] == [
        "Jorge Martinez",
        "Mindy Miller",
        "Aaron Neyer",
        "Alejandra Nieland",
        "Carlos Valdez",
    ]


def test_boulder_titles_that_are_a_kind_of_seat():
    assert _members("boulder-university-hill.html") == [
        ("Dane Anderson", "Vice Chair"),
        ("Trent Bush", "Representative"),
        ("Ben Holland", "Resident"),
        ("Tell Jones", "Representative"),
        ("Danica Powell", "Chair"),
    ]


def test_louisville_prints_plain_names_under_a_year_and_board_heading():
    members = _members("louisville-library-board-of-trustees.html")
    assert members[0] == ("Jeanne Baker", "Member")
    assert members[-2:] == [
        ("Cheryl Lynn Achterberg", "Representative"),
        ("Carol Burkett", "Alternate"),
    ]
    assert len(members) == 8  # the sentence after the list ends it


def test_county_prints_the_title_before_the_term_and_the_staff_after_the_members():
    members = _members("boulder-county-board-of-adjustment.html")
    assert members == [
        ("David Marlett", "Chair"),
        ("Kent Campbell", "Vice Chair"),
        ("Jean “Raini” Ott", "Member"),
        ("Robert Ukeiley", "Member"),
        ("Finn Jackson", "Member"),
    ]  # Dale Case, Director, and the others under "Staff Participants" are staff
    posac = _members("boulder-county-posac.html")
    assert posac[0] == ("Trace Baker", "Member") and len(posac) == 9


def test_lafayette_term_dates_with_a_day_and_liaisons_with_a_misspelt_title():
    library = _members("lafayette-library-board.html")
    assert library[:2] == [("Jody Tracy", "Member"), ("Jessica Gribble", "Member")]
    assert len(library) == 9  # each row ends in a date like "Nov. 30, 2026"
    youth = _members("lafayette-youth-advisory-board.html")
    names = [n for n, _ in youth]
    assert names[0] == "David Brady" and len(youth) == 12
    # The council liaison and the staff liaisons ("Staff Liason" once) head the table.
    assert "Crystal Gallegos" not in names and "Melissa Hisel" not in names


def test_a_liaison_above_the_members_and_page_furniture_below_them_are_not_members():
    members = _members("lafayette-downtown-development-authority.html")
    assert [n for n, _ in members] == [
        "Faith Rodgers",
        "Genae Lako",
        "Ian Munding",
        "Tom Hardie",
        "Ty Hubbard",
        "Joanna Cagan",
    ]  # Tim Barnes is the council liaison, Brigid Keating the director, "Quick Links" a heading


def test_an_open_seat_is_not_a_member_and_a_recorder_is():
    assert _members("lyons-student-advisory-commission.html") == [
        ("Nico Sparks", "Chair"),
        ("Wilma Spencer", "Vice Chair"),
        ("Reagan Friedman", "Recorder"),
        ("Kseniya Rumnakh", "Member"),
        ("Anush Amirjanyan", "Member"),
        ("Luke Vasquez", "Member"),
    ]  # "Open" is a seat with no one in it; the trustee liaison is last


def test_a_heading_under_the_last_member_is_not_that_members_title():
    members = _members("lyons-board-of-adjustments.html")
    assert members[-1] == ("Hollie Rogin", "Member")  # the "Form" heading comes next
    assert len(members) == 7


def test_a_title_joined_to_a_name_with_a_hyphen_is_not_a_member():
    names = [n for n, _ in _members("lyons-arts-humanities-commission.html")]
    assert names[-1] == "Kylee Covili" and len(names) == 7
    # "Chrystal DeCoster-Town Art Show Curator" is last, in a list of its own.


def test_a_page_with_fewer_than_three_names_has_no_roster():
    page = "<h2>Members</h2><ul><li>Ann Lee, Chair</li><li>Bo Ray</li></ul>"
    assert boardsites.read_board_members(page) == []


@pytest.mark.parametrize(
    ("body", "link", "score"),
    [
        ("BJAD Parking Commission", "Boulder Junction Access District Parking Commission", 3),
        ("BJAD TDM Commission", "Boulder Junction Access District Parking Commission", 0),
        ("Boulder Urban Renewal Authority", "Boulder Urban Renewal Authority (BURA)", 3),
        ("Open Space and Trails Advisory Board", "Open Space & Trails Advisory Board", 3),
        ("Board of Adjustment/Board of Appeals", "Board of Adjustment", 3),
        ("Lafayette Cultural Arts Board", "Cultural Arts Board", 2),
        ("Planning Commission", "Join the Planning Commission", 0),
    ],
)
def test_a_link_names_a_board(body, link, score):
    assert boardsites._score(body, link) == score


# ----- A town's site ---------------------------------------------------------------------------

SITE = "https://www.exampleco.gov"
BOARDS_INDEX = SITE + "/10/Boards-Commissions"


def _page(title: str, body: str) -> str:
    head = f"<title>{title} | Example, CO</title>"
    return f"<html><head>{head}</head><body><h1>{title}</h1>{body}</body></html>"


def _list(*names: str) -> str:
    return "<h2>Members</h2><ul>" + "".join(f"<li>{n}</li>" for n in names) + "</ul>"


def _town(tmp_path, web, bodies):
    folder = tmp_path / "example"
    folder.mkdir()
    source = make_source("civicplus", {"site": "www.exampleco.gov", "bodies": bodies})
    merge_sources(folder, [source])
    (folder / "city.json").write_text(json.dumps({"name": "Example"}))
    (folder / "roster.json").write_text(json.dumps({"state": "CO", "body": "Town Council"}))
    (folder / "city-search.json").write_text(json.dumps({"website": SITE + "/"}))
    links = '<a href="/10/Boards-Commissions">Boards &amp; Commissions</a>'
    web.add(SITE + "/", _page("Home", links))
    web.add(
        BOARDS_INDEX,
        _page(
            "Boards",
            '<a href="/11/Library-Board">Library Board</a>'
            '<a href="/12/Older-Adults-Advisory-Board">Older Adults Advisory Board</a>'
            '<a href="/13/Join-the-Library-Board">Join the Library Board</a>',
        ),
    )
    web.add(
        SITE + "/11/Library-Board",
        _page(
            "Library Board", _list("Ann Lee, Chair", "Bo Ray", "Cy Fox", "Dee Roe, Staff Liaison")
        ),
    )
    web.add(
        SITE + "/12/Older-Adults-Advisory-Board",
        _page("Older Adults Advisory Board", _list("Ed Moe", "Flo Poe", "Gus Roe")),
    )
    return folder


def _run(folder, web, monkeypatch):
    monkeypatch.setattr(boardsites, "PAUSE_S", 0)
    cli._rosters(argparse.Namespace(city=folder), fetcher=web.fetcher())


def test_city_rosters_writes_a_board_from_its_page_on_the_towns_site(
    tmp_path, web, monkeypatch, capsys
):
    folder = _town(tmp_path, web, ["Town Council", "Library Board"])
    _run(folder, web, monkeypatch)
    path = roster_for(folder, "Library Board")
    assert path == folder / "rosters" / "library-board.json"
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert [m["name"] for m in data["members"]] == ["Ann Lee", "Bo Ray", "Cy Fox"]
    assert data["sources"] == [SITE + "/11/Library-Board"]
    assert "@" not in text
    assert "Library Board: wrote library-board.json, 3 members" in capsys.readouterr().out


def test_a_guessed_address_that_answers_with_another_boards_page_gets_no_roster(
    tmp_path, web, monkeypatch, capsys
):
    # The site answers any address under a board's page with that board's page (Lafayette's did).
    folder = _town(tmp_path, web, ["Town Council", "Cemetery Subcommittee"])
    web.add(
        BOARDS_INDEX + "/cemetery-subcommittee/",
        _page("Older Adults Advisory Board", _list("Ed Moe", "Flo Poe", "Gus Roe")),
    )
    _run(folder, web, monkeypatch)
    assert roster_for(folder, "Cemetery Subcommittee") is None
    out = capsys.readouterr().out
    assert "Cemetery Subcommittee: no member list found; checked " + BOARDS_INDEX in out


def test_a_board_name_that_sounds_like_the_index_is_not_the_index(tmp_path, web, monkeypatch):
    # "Older Adults Advisory Board" is a board, not the town's list of boards.
    folder = _town(tmp_path, web, ["Town Council", "Older Adults Advisory Board"])
    web.add(
        SITE + "/",
        _page(
            "Home",
            '<a href="/10/Boards-Commissions">Boards &amp; Commissions</a>'
            '<a href="/12/Older-Adults-Advisory-Board">Older Adults Advisory Board</a>',
        ),
    )
    _run(folder, web, monkeypatch)
    assert SITE + "/12/Older-Adults-Advisory-Board/older-adults-advisory-board/" not in web.asked
    assert roster_for(folder, "Older Adults Advisory Board") is not None


def test_a_board_with_members_already_is_not_changed(tmp_path, web, monkeypatch):
    folder = _town(tmp_path, web, ["Town Council", "Library Board"])
    (folder / "rosters").mkdir()
    mine = folder / "rosters" / "library-board.json"
    mine.write_text(json.dumps({"body": "Library Board", "members": [{"name": "Zed Zip"}]}))
    _run(folder, web, monkeypatch)
    assert "Zed Zip" in mine.read_text()


def test_two_boards_never_get_the_same_page(tmp_path, web, monkeypatch, capsys):
    folder = _town(tmp_path, web, ["Town Council", "Library Board", "Library Board of Trustees"])
    web.add(
        BOARDS_INDEX,
        _page(
            "Boards",
            '<a href="/11/Library-Board">Library Board</a>'
            '<a href="/11/Library-Board">Library Board of Trustees</a>',
        ),
    )
    _run(folder, web, monkeypatch)
    assert roster_for(folder, "Library Board") is not None
    assert roster_for(folder, "Library Board of Trustees") is None
    assert "already Library Board's" in capsys.readouterr().out
