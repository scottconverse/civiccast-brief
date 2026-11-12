"""Board rosters read from a PrimeGov portal's member lists (Longmont's, saved 2026-11-11)."""

from __future__ import annotations

import argparse
import json
from datetime import date

from civiccast_brief.citybuild import boards, cli
from civiccast_brief.sources import make_source, merge_sources
from civiccast_brief.weekly.run import roster_for

from .conftest import HERE, PRIMEGOV

MEMBERS_6 = PRIMEGOV + "/api/v1/CommitteeManager/Portal/GetMembers?committeeId=6"
TODAY = date(2026, 11, 11)


def _rows():
    return json.loads((HERE / "primegov-members-6.json").read_text(encoding="utf-8"))


def test_only_current_members_are_kept_with_their_titles():
    members = boards.members_from(_rows(), TODAY)
    assert members == [
        {"name": "Amy Saunders", "first": "Amy", "last": "Saunders", "role": "Board Member"},
        {"name": "Harrison Earl", "first": "Harrison", "last": "Earl", "role": "Vice Chair"},
        {"name": "Michael Polan", "first": "Michael", "last": "Polan", "role": "Chair"},
        {"name": "Ryan Forbes", "first": "Ryan", "last": "Forbes", "role": "Alternate Board Member"},
        # The portal prints a line break inside this name.
        {"name": 'Liang "Warren" Wang', "first": 'Liang "Warren"', "last": "Wang", "role": "Alternate Board Member"},
    ]  # fmt: skip
    # Pat Former's term ended in 2024, and seat 9 is vacated.


def _city(tmp_path, web):
    web.fixture(
        PRIMEGOV + "/api/committee/GetCommitteeesListByShowInPublicPortal",
        "primegov-committees-longmont.json",
        "application/json",
    )
    web.fixture(MEMBERS_6, "primegov-members-6.json", "application/json")
    folder = tmp_path / "longmont"
    folder.mkdir()
    bodies = ["City Council", "Planning and Zoning Commission", "Water Board"]
    merge_sources(folder, [make_source("primegov", {"url": PRIMEGOV, "bodies": bodies})])
    (folder / "city.json").write_text(json.dumps({"slug": "longmont", "name": "Longmont"}))
    roster = {"city": "Longmont", "state": "Colorado", "body": "City Council", "members": []}
    (folder / "roster.json").write_text(json.dumps(roster))
    return folder


def test_city_rosters_writes_each_boards_roster_the_weekly_run_reads(tmp_path, web, capsys):
    folder = _city(tmp_path, web)
    assert roster_for(folder, "Planning and Zoning Commission") is None
    code = cli._rosters(argparse.Namespace(city=folder), fetcher=web.fetcher())
    assert code == 0
    path = roster_for(folder, "Planning and Zoning Commission")
    assert path == folder / "rosters" / "planning-and-zoning-commission.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (data["city"], data["state"], data["body"]) == ("Longmont", "Colorado", "Planning and Zoning Commission")  # fmt: skip
    assert data["sources"] == [PRIMEGOV + "/public/committees"]
    assert len(data["members"]) == 5
    # The portal's member list carries email addresses; none is kept.
    assert "@" not in path.read_text(encoding="utf-8")
    # The council keeps roster.json, and the Water Board's member list did not answer.
    assert not (folder / "rosters" / "city-council.json").exists()
    out = capsys.readouterr().out
    assert (
        "Planning and Zoning Commission: wrote planning-and-zoning-commission.json, 5 members"
        in out
    )
    assert "Water Board: the member list could not be read" in out


def test_a_board_roster_with_members_is_not_changed(tmp_path, web, capsys):
    folder = _city(tmp_path, web)
    own = folder / "rosters" / "planning-and-zoning-commission.json"
    own.parent.mkdir()
    own.write_text(
        json.dumps({"body": "Planning and Zoning Commission", "members": [{"name": "A B"}]})
    )
    before = own.read_text()
    cli._rosters(argparse.Namespace(city=folder), fetcher=web.fetcher())
    assert own.read_text() == before
    assert "kept planning-and-zoning-commission.json" in capsys.readouterr().out


# ----- School boards ---------------------------------------------------------------------------

SVVSD_BOARD = "https://www.svvsd.org/about/board-of-education/"
SVVSD_MEETINGS = SVVSD_BOARD + "board-meetings/2026-board-meetings/"


def _page(name):
    return (HERE / name).read_text(encoding="utf-8")


def test_a_school_board_page_gives_each_director_with_office_and_district():
    svvsd = boards.read_district_members(_page("svvsd-board-of-education.html"))
    assert [(m["name"], m["role"]) for m in svvsd] == [
        ("Jocelyn Gilligan", "President, District E"),
        ("Jim Berthold", "Vice President, District C"),
        ("Sarah Hurianek", "Secretary, District F"),
        ("Jacqueline Weiss", "Treasurer, District A"),  # her names are on two lines
        ("Geno Lechuga", "Assistant Secretary, District G"),
        ("Meosha Babbs", "Board Member, District D"),
        ("Hadley Solomon", "Board Member, District B"),
    ]
    bvsd = boards.read_district_members(_page("bvsd-meet-the-board.html"))
    assert [(m["name"], m["role"]) for m in bvsd][:3] == [
        ("Nicole Rajpal", "President, District B"),
        ("Alex Medler", "Vice President, District C"),
        ("Jason Unger", "Treasurer, District A"),
    ]
    # A name with a letter past A-Z is a name.
    assert bvsd[-1] == {"name": "Jorge Chávez", "first": "Jorge", "last": "Chávez", "role": "Board Member, District G"}  # fmt: skip


def _school_city(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    source = make_source("webpage", {"url": SVVSD_MEETINGS, "bodies": ["Board of Education"]})
    merge_sources(folder, [source])
    (folder / "city.json").write_text(json.dumps({"name": name.title()}))
    (folder / "roster.json").write_text(json.dumps({"state": "Colorado", "body": "City Council"}))
    return folder


def test_the_meetings_page_leads_up_to_the_boards_members(tmp_path, web, capsys):
    web.add(SVVSD_BOARD, _page("svvsd-board-of-education.html"))
    folder = _school_city(tmp_path, "lyons")
    cli._rosters(argparse.Namespace(city=folder), fetcher=web.fetcher())
    path = roster_for(folder, "Board of Education")
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["sources"] == [SVVSD_BOARD] and len(data["members"]) == 7
    # The meetings page was read first, then the page above it.
    assert web.asked[:2] == [SVVSD_MEETINGS, SVVSD_MEETINGS.rsplit("/", 2)[0] + "/"]


def test_a_town_sharing_the_boards_source_gets_the_same_roster(tmp_path, web):
    web.add(SVVSD_BOARD, _page("svvsd-board-of-education.html"))
    lyons = _school_city(tmp_path, "lyons")
    cli._rosters(argparse.Namespace(city=lyons), fetcher=web.fetcher())
    erie = _school_city(tmp_path, "erie")
    web.pages.clear()  # the district's site is down; the roster Lyons wrote is used
    cli._rosters(argparse.Namespace(city=erie), fetcher=web.fetcher())
    ours = roster_for(erie, "Board of Education")
    assert ours is not None
    assert ours.read_bytes() == (lyons / "rosters" / "board-of-education.json").read_bytes()
