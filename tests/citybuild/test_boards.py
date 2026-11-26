"""Board rosters read from a portal's member lists (Longmont's PrimeGov, Loveland's CivicWeb)."""

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


# ----- A CivicWeb portal's member pages (Loveland's, saved 2026-11-25) --------------------------

CIVICWEB = "https://cilovelandco.civicweb.net"
CIVICWEB_TYPES = CIVICWEB + "/Portal/MeetingTypeList.aspx"
PLANNING_MEMBERS = CIVICWEB + "/portal/members.aspx?id=58"
LOVELAND_TODAY = date(2026, 11, 25)

#: The Planning Commission's members, in the portal's order.
LOVELAND_PLANNING = [
    ("Sarah Warnock", "06 Jan 2026 - 31 Dec 2028"),
    ("Julia Fitzpatrick", "15 Sep 2026 - 31 Dec 2027"),
    ("David Powell", "06 Jan 2026 - 31 Dec 2028"),
    ("Kiel Schuett", "01 Jan 2024 - 29 Dec 2026"),
    ("Tracy Geraghty", "15 Sep 2026 - 31 Dec 2026"),
    ("Tammy Fisher", "06 Jan 2026 - 31 Dec 2028"),
    ("Gregory Dewey", "07 Jan 2025 - 31 Dec 2027"),
    ("Barry Long", "02 Apr 2025 - 31 Dec 2027"),
    ("David Herrera", "17 Jan 2024 - 31 Dec 2026"),
    ("Troy Bliss", "01 Oct 2025 - 01 Jan 2028"),
    ("Geoff Frahm", "02 Dec 2025 - Ongoing"),
]


def _civicweb(web):
    web.fixture(CIVICWEB_TYPES, "civicweb-loveland-meeting-types.html")
    web.fixture(PLANNING_MEMBERS, "civicweb-loveland-members-58.html")


def _civicweb_city(tmp_path, bodies):
    folder = tmp_path / "loveland"
    folder.mkdir()
    merge_sources(folder, [make_source("civicweb", {"tenant": "cilovelandco", "bodies": bodies})])
    (folder / "city.json").write_text(json.dumps({"slug": "loveland", "name": "Loveland"}))
    (folder / "roster.json").write_text(
        json.dumps({"city": "Loveland", "state": "Colorado", "body": "City Council", "members": []})
    )
    return folder


def _source(bodies):
    return make_source("civicweb", {"tenant": "cilovelandco", "bodies": bodies})


def test_civicweb_members_page_gives_each_current_member(tmp_path, web):
    _civicweb(web)
    folder = _civicweb_city(tmp_path, ["Planning Commission"])
    lines = boards.write_civicweb_rosters(
        folder, _source(["Planning Commission"]), "Loveland", "Colorado", web.fetcher(), LOVELAND_TODAY
    )  # fmt: skip
    path = folder / "rosters" / "planning-commission.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [(m["name"], m["role"]) for m in data["members"]] == [
        (name, "City Council Member Ward I" if name == "Geoff Frahm" else "Member")
        for name, _term in LOVELAND_PLANNING
    ]
    assert data["members"][0] == {
        "name": "Sarah Warnock",
        "first": "Sarah",
        "last": "Warnock",
        "role": "Member",
    }
    assert data["sources"] == [PLANNING_MEMBERS]
    assert (data["city"], data["state"], data["body"]) == ("Loveland", "Colorado", "Planning Commission")  # fmt: skip
    assert "as the city's CivicWeb portal lists them" in data["note"]
    assert lines == [f"Planning Commission: wrote planning-commission.json, 11 members from {PLANNING_MEMBERS}"]  # fmt: skip


def test_a_civicweb_member_whose_term_ended_is_dropped(tmp_path, web):
    _civicweb(web)
    folder = _civicweb_city(tmp_path, ["Planning Commission"])
    boards.write_civicweb_rosters(
        folder, _source(["Planning Commission"]), "Loveland", "Colorado", web.fetcher(), date(2027, 1, 5)
    )  # fmt: skip
    data = json.loads((folder / "rosters" / "planning-commission.json").read_text(encoding="utf-8"))
    names = [m["name"] for m in data["members"]]
    assert len(names) == 8
    # Their terms ended on 29 and 31 December 2026.
    assert "Kiel Schuett" not in names
    assert "Tracy Geraghty" not in names
    assert "David Herrera" not in names


#: A members page whose list holds city staff beside the board's own members (the Construction
#: Advisory Board's and the Open Lands and Trails Advisory Commission's, as Loveland lists them).
CIVICWEB_MEMBERS_WITH_STAFF = """<!doctype html><html><body>
<h2 id="ctl00_MainContent_MembersRepeater_ctl00_Name" class="member-name">Theresa Campbell</h2>
<span id="ctl00_MainContent_MembersRepeater_ctl00_Title">Staff Liaison</span>
<span id="ctl00_MainContent_MembersRepeater_ctl00_Term">06 Jan 2026 - 31 Dec 2028</span>
<h2 id="ctl00_MainContent_MembersRepeater_ctl01_Name" class="member-name">Marilyn Hilgenberg</h2>
<span id="ctl00_MainContent_MembersRepeater_ctl01_Title">Open Lands &amp; Trails Manager</span>
<span id="ctl00_MainContent_MembersRepeater_ctl01_Term">06 Jan 2026 - 31 Dec 2028</span>
<h2 id="ctl00_MainContent_MembersRepeater_ctl02_Name" class="member-name">Geoff Frahm</h2>
<span id="ctl00_MainContent_MembersRepeater_ctl02_Title">City Council Member Ward I</span>
<span id="ctl00_MainContent_MembersRepeater_ctl02_Term">02 Dec 2025 - Ongoing</span>
<h2 id="ctl00_MainContent_MembersRepeater_ctl03_Name" class="member-name">Barry Long</h2>
<span id="ctl00_MainContent_MembersRepeater_ctl03_Title"></span>
<span id="ctl00_MainContent_MembersRepeater_ctl03_Term">02 Apr 2025 - 31 Dec 2027</span>
</body></html>"""


def test_a_civicweb_members_page_leaves_staff_out():
    members = boards.members_from_civicweb(CIVICWEB_MEMBERS_WITH_STAFF, LOVELAND_TODAY)
    assert members == [
        {
            "name": "Geoff Frahm",
            "first": "Geoff",
            "last": "Frahm",
            "role": "City Council Member Ward I",
        },
        {"name": "Barry Long", "first": "Barry", "last": "Long", "role": "Member"},
    ]


def test_a_civicweb_body_with_a_roster_is_left_alone(tmp_path, web):
    _civicweb(web)
    folder = _civicweb_city(tmp_path, ["Planning Commission"])
    own = folder / "rosters" / "planning-commission.json"
    own.parent.mkdir()
    own.write_text(json.dumps({"body": "Planning Commission", "members": [{"name": "A B"}]}))
    lines = boards.write_civicweb_rosters(
        folder, _source(["Planning Commission"]), "Loveland", "Colorado", web.fetcher(), LOVELAND_TODAY
    )  # fmt: skip
    assert lines == []
    assert PLANNING_MEMBERS not in web.asked


def test_a_civicweb_body_with_no_member_page_says_so(tmp_path, web):
    _civicweb(web)
    folder = _civicweb_city(tmp_path, ["Water Board"])
    lines = boards.write_civicweb_rosters(
        folder, _source(["Water Board"]), "Loveland", "Colorado", web.fetcher(), LOVELAND_TODAY
    )  # fmt: skip
    assert lines == ["Water Board: the portal lists no member page for it"]
    assert not (folder / "rosters").exists()


def test_a_civicweb_members_page_that_cannot_be_read_is_one_line(tmp_path, web):
    web.fixture(CIVICWEB_TYPES, "civicweb-loveland-meeting-types.html")
    folder = _civicweb_city(tmp_path, ["Planning Commission"])
    lines = boards.write_civicweb_rosters(
        folder, _source(["Planning Commission"]), "Loveland", "Colorado", web.fetcher(), LOVELAND_TODAY
    )  # fmt: skip
    assert len(lines) == 1
    assert lines[0].startswith("Planning Commission: the member list could not be read")
    assert not (folder / "rosters").exists()


def test_a_civicweb_roster_keeps_no_address(tmp_path, web):
    _civicweb(web)
    folder = _civicweb_city(tmp_path, ["Planning Commission"])
    boards.write_civicweb_rosters(
        folder, _source(["Planning Commission"]), "Loveland", "Colorado", web.fetcher(), LOVELAND_TODAY
    )  # fmt: skip
    text = (folder / "rosters" / "planning-commission.json").read_text(encoding="utf-8")
    assert "@" not in text and "mailto" not in text


def test_city_rosters_reads_a_civicweb_portal(tmp_path, web):
    _civicweb(web)
    folder = _civicweb_city(tmp_path, ["City Council", "Planning Commission"])
    lines = cli.write_city_rosters(
        folder, fetcher=web.fetcher(), open_page=lambda url: None, close_chrome=lambda: None
    )
    assert (folder / "rosters" / "planning-commission.json").exists()
    # The council's meeting type finds roster.json, and no line is written for it.
    assert not (folder / "rosters" / "city-council.json").exists()
    assert not any(line.startswith("City Council:") for line in lines)


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


# ----- a roster entered on the Roster page is kept by every writer -----------------------------


HAND_NOTE = "; its members were entered by hand"


def test_the_primegov_writer_keeps_a_hand_entered_roster(tmp_path, web, capsys):
    folder = _city(tmp_path, web)
    own = folder / "rosters" / "planning-and-zoning-commission.json"
    own.parent.mkdir()
    own.write_text(
        json.dumps(
            {
                "body": "Planning and Zoning Commission",
                "hand": True,
                "members": [{"name": "Ada Lovelace", "source": "https://example.org/ada"}],
            }
        )
    )
    before = own.read_text()
    cli._rosters(argparse.Namespace(city=folder), fetcher=web.fetcher())
    assert own.read_text() == before
    out = capsys.readouterr().out
    assert (
        "Planning and Zoning Commission: kept planning-and-zoning-commission.json" + HAND_NOTE
        in out
    )


def test_the_civicweb_writer_keeps_a_hand_entered_roster(tmp_path, web):
    _civicweb(web)
    folder = _civicweb_city(tmp_path, ["Planning Commission"])
    path = folder / "rosters" / "planning-commission.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "body": "Planning Commission",
                "hand": True,
                "members": [{"name": "Ada Lovelace", "source": "https://example.org/ada"}],
            }
        )
    )
    before = path.read_text()
    lines = boards.write_civicweb_rosters(
        folder, _source(["Planning Commission"]), "Loveland", "Colorado", web.fetcher(), LOVELAND_TODAY
    )  # fmt: skip
    assert path.read_text() == before
    assert lines == ["Planning Commission: kept planning-commission.json" + HAND_NOTE]
