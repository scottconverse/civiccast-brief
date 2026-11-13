"""The roster filled from the meeting's own records during the weekly run, and shown on the desk."""

import json
from datetime import date
from pathlib import Path

from desk_signin import signed_in
from test_weekly_run import COUNCIL, LAST, Fake, _settings

from civiccast_brief.meeting.roster import load_roster
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app
from civiccast_brief.weekly import rosterfill
from civiccast_brief.weekly.find import Found
from civiccast_brief.weekly.run import STATE_NAME, run_week

PAGE = """MINUTES
City Council Regular Session
October 20, 2026
1.
MEETING CALLED TO ORDER
2.
ROLL CALL - PLEDGE OF ALLEGIANCE
Dawn Quintana, City Clerk, called the roll. Those present were Mayor Susie Hidalgo-
Fahring and Council Members Diane Crist, Alex Kalkhofer, and Sean McCoy.
3.
APPROVAL OF MINUTES"""
DOCUMENTS = {
    "format": 1,
    "documents": [
        {
            "kind": "packet",
            "url": "https://longmont.primegov.com/Public/CompiledDocument?x=1",
            "sha256": "0",
            "pages": [{"page": 6, "text": PAGE}],
        }
    ],
}
DAY = date(2026, 11, 3)


def meeting(documents=DOCUMENTS):
    return Found(
        COUNCIL,
        DAY,
        "City Council Regular Session",
        "p",
        "oWZpzjzjwnE",
        documents=(lambda: (documents, ["packet: 1 pages"])) if documents else None,
    )


def test_the_run_writes_the_roster_from_the_roll_call_before_the_recap_reads_it(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path)
    seen = []
    real_meeting = fake.meeting

    def meeting_step(argv):
        roster = json.loads(Path(argv[argv.index("--roster") + 1]).read_text(encoding="utf-8"))
        seen.append([m["name"] for m in roster["members"]])
        return real_meeting(argv)

    fake.meeting = meeting_step
    report = run_week(settings, [meeting()], LAST, fake.steps())

    assert report.failed == 0, report.lines
    # The roster the recap step is given already holds the members the minutes printed.
    assert seen == [["Susie Hidalgo-Fahring", "Diane Crist", "Alex Kalkhofer", "Sean McCoy"]]
    data = json.loads((settings.city / "roster.json").read_text(encoding="utf-8"))
    crist = data["members"][1]
    assert (crist["name"], crist["first"], crist["last"], crist["role"]) == (
        "Diane Crist",
        "Diane",
        "Crist",
        "Council Member",
    )
    assert crist["seen"] == ["2026-10-20"]
    (record,) = crist["records"]
    assert record["document"] == "packet"
    assert record["url"] == "https://longmont.primegov.com/Public/CompiledDocument?x=1#page=6"
    assert (record["page"], record["line"], record["date"]) == (6, 8, "2026-10-20")
    assert any("roster City Council: 4 members read from the records" in x for x in report.lines)
    state = json.loads((settings.work / STATE_NAME).read_text(encoding="utf-8"))
    assert state["meetings"]["youtube:oWZpzjzjwnE"]["done"][:3] == ["documents", "roster", "recap"]


def test_a_body_with_no_roster_file_gets_one_made_from_its_records(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path)
    (settings.city / "city.json").write_text('{"name": "Longmont"}', encoding="utf-8")
    page = PAGE.replace("City Council Regular Session", "Planning and Zoning Commission")
    page = page.replace("Council Members", "Commissioners")
    documents = {"documents": [{**DOCUMENTS["documents"][0], "pages": [{"page": 2, "text": page}]}]}
    found = Found(
        "Planning and Zoning Commission",
        DAY,
        "Planning and Zoning Commission",
        "p",
        "PZPZPZPZPZ1",
        documents=lambda: (documents, []),
    )
    run_week(settings, [found], LAST, fake.steps())

    path = settings.city / "rosters" / "planning-and-zoning-commission.json"
    roster = load_roster(path)  # the file format the speaker naming reads
    assert roster.body == "Planning and Zoning Commission"
    assert roster.names() == [
        "Susie Hidalgo-Fahring",
        "Diane Crist",
        "Alex Kalkhofer",
        "Sean McCoy",
    ]
    assert fake.calls[0][1][fake.calls[0][1].index("--roster") + 1] == str(path)


def test_no_list_in_the_records_leaves_the_roster_and_says_so(tmp_path):
    fake = Fake()
    settings = _settings(tmp_path)
    before = (settings.city / "roster.json").read_text(encoding="utf-8")
    quiet = {
        "documents": [{"kind": "packet", "url": "u", "pages": [{"page": 1, "text": "PACKET"}]}]
    }
    report = run_week(settings, [meeting(quiet)], LAST, fake.steps())

    assert (settings.city / "roster.json").read_text(encoding="utf-8") == before
    assert (
        "2026-11-03 City Council Regular Session: "
        "no member list found in the records for City Council 2026-11-03"
    ) in report.lines


def test_a_failed_meeting_still_reports_that_no_member_list_was_found(tmp_path):
    settings = _settings(tmp_path)
    found = Found("Water Board", DAY, "Water Board", "p", "WATERWATER01")
    report = run_week(settings, [found], LAST, Fake().steps())

    assert report.failed == 1
    assert (
        "2026-11-03 Water Board: no member list found in the records for Water Board 2026-11-03"
        in (report.lines)
    )


def _roster(tmp_path, members):
    path = tmp_path / "roster.json"
    data = {"city": "Longmont", "state": "Colorado", "body": COUNCIL, "members": members}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_hand_entry_is_kept_untouched_and_gains_only_the_date_and_source(tmp_path):
    hand = {
        "name": "Diane Crist",
        "first": "Diane",
        "last": "Crist",
        "role": "Ward 1 Representative",
    }
    other = {"name": "Old Member", "first": "Old", "last": "Member", "role": "Mayor"}
    path = _roster(tmp_path, [hand, other])
    lines = rosterfill.fill_roster(path, tmp_path, COUNCIL, DAY, DOCUMENTS)

    members = json.loads(path.read_text(encoding="utf-8"))["members"]
    assert [m["name"] for m in members] == [
        "Diane Crist",
        "Old Member",  # nobody's roll call lists him; a hand entry is never deleted
        "Susie Hidalgo-Fahring",
        "Alex Kalkhofer",
        "Sean McCoy",
    ]
    crist = members[0]
    assert crist["role"] == "Ward 1 Representative"  # the entry is not rewritten
    assert crist["seen"] == ["2026-10-20"]
    assert "records" not in members[1]
    assert lines[0].endswith(
        "3 added (Susie Hidalgo-Fahring, Alex Kalkhofer, Sean McCoy), 1 already listed"
    )


def test_a_second_read_of_the_same_records_changes_nothing(tmp_path):
    path = _roster(tmp_path, [])
    rosterfill.fill_roster(path, tmp_path, COUNCIL, DAY, DOCUMENTS)
    once = path.read_text(encoding="utf-8")
    lines = rosterfill.fill_roster(path, tmp_path, COUNCIL, DAY, DOCUMENTS)
    assert path.read_text(encoding="utf-8") == once
    assert lines[0].endswith("0 added (none), 4 already listed; nothing new to write")


def test_a_later_meeting_marks_the_new_date_seen(tmp_path):
    path = _roster(tmp_path, [])
    rosterfill.fill_roster(path, tmp_path, COUNCIL, DAY, DOCUMENTS)
    later = json.loads(json.dumps(DOCUMENTS).replace("October 20, 2026", "November 3, 2026"))
    rosterfill.fill_roster(path, tmp_path, COUNCIL, date(2026, 11, 10), later)
    crist = json.loads(path.read_text(encoding="utf-8"))["members"][1]
    assert crist["seen"] == ["2026-10-20", "2026-11-03"]
    assert len(crist["records"]) == 2


def test_the_desk_roster_page_says_where_each_member_came_from_and_has_no_editor(tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    path = _roster(
        city, [{"name": "Hand Entry", "first": "Hand", "last": "Entry", "role": "Mayor"}]
    )
    rosterfill.fill_roster(path, city, COUNCIL, DAY, DOCUMENTS)
    with ReviewStore(tmp_path / "d.db") as store:
        client = signed_in(make_desk_app(store, city_dir=city))
        page = client.get("/roster")
        home = client.get("/")
    assert page.status_code == 200
    assert 'href="/roster"' in home.text
    assert "Nobody types the members in" in page.text
    assert "Diane Crist" in page.text
    assert "packet, page 6, line 8" in " ".join(page.text.split())
    assert "Entered by hand" in page.text
    main = page.text.split("<main>", 1)[1]
    assert "<form" not in main and "<textarea" not in main
    assert "@" not in page.text


def test_a_record_name_that_is_a_listed_members_short_form_is_that_member_not_a_new_one(tmp_path):
    # Longmont's PrimeGov list prints "Andrew H Jordan" and `Liang "Warren" Wang`; the minutes
    # print "Andy Jordan" and "Warren Wang".
    listed = [
        {"name": "Andrew H Jordan", "first": "Andrew", "last": "Jordan", "role": "Board Member"},
        {"name": 'Liang "Warren" Wang', "first": "Liang", "last": "Wang", "role": "Alternate"},
        {"name": "Jane Smith", "first": "Jane", "last": "Smith", "role": "Board Member"},
    ]
    path = _roster(tmp_path, listed)
    page = PAGE.replace(
        "Diane Crist, Alex Kalkhofer, and Sean McCoy", "Andy Jordan, Warren Wang, and Jim Smith"
    )
    docs = {"documents": [{**DOCUMENTS["documents"][0], "pages": [{"page": 6, "text": page}]}]}
    rosterfill.fill_roster(path, tmp_path, COUNCIL, DAY, docs)

    members = {m["name"]: m for m in json.loads(path.read_text(encoding="utf-8"))["members"]}
    assert list(members) == [
        "Andrew H Jordan",
        'Liang "Warren" Wang',
        "Jane Smith",
        "Susie Hidalgo-Fahring",
        "Jim Smith",  # Jim is not Jane: a different first name is a different person
    ]
    assert members["Andrew H Jordan"]["printed_as"] == ["Andy Jordan"]
    assert members['Liang "Warren" Wang']["seen"] == ["2026-10-20"]
    assert "seen" not in members["Jane Smith"]
