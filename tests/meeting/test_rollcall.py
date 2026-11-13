"""Members read from the roll call of a body's own minutes.

The page texts are the ones Longmont's published records print (the council's packet of
2026-11-03 holds the 2026-10-20 minutes; the Planning and Zoning packet of 2026-10-21 holds the
2026-09-16 minutes; a council study session packet holds the Sustainability Advisory Board's
2026-09-16 minutes), cut to the lines the roll call needs.
"""

from datetime import date

from civiccast_brief.meeting import rollcall

COUNCIL_PAGE = """MINUTES
City Council Regular Session
October 20, 2026
City Council Chambers, 350 Kimbark St., Longmont, CO

1.
MEETING CALLED TO ORDER
The October 20, 2026, Regular Session of the Longmont City Council was called
to order at 7:00 p.m. by Mayor Susie Hidalgo-Fahring in the City Council
Chambers.
2.
ROLL CALL - PLEDGE OF ALLEGIANCE

Dawn Quintana, City Clerk, called the roll. Those present were Mayor Susie Hidalgo-
Fahring and Council Members Diane Crist, Alex Kalkhofer, Jake Marsing, Sean McCoy,
Matthew Popkin, and Crystal Prieto.
City Council Regular Session, October 20, 2026
Page 1"""

# The packet prints a line number in front of every line of these minutes.
PZ_LINES = [
    "MINUTES",
    "PLANNING AND ZONING COMMISSION",
    "MAY 20, 2026",
    "",
    "",
    "1. Call To Order",
    "",
    "Chair Michael Polan called the May 20, 2026, meeting of the Planning and Zoning",
    "to order at 7:00p.m., in City Council Chambers.",
    "",
    "2. Roll Call",
    "",
    "Recording Assistant Jane Madrid called the roll. Present on the Commission were Jacob Arment,",
    "Tom Lange, Warren Wang, Michael Polan, Amy Saunders, Ryan Forbes, and Andy Jordan.",
    "Commissioners Devin Edgley-Wells, Harrison Earl, and Geri Boone, and Council Representative",
    "Matthew Popkin were absent. In attendance also was Planning Director Grant Penland and",
    "Senior Assistant City Attorney Jeremy Tyrrell.",
    "",
    "Other staff present:  Senior Planner Kristin Cote, Civil Engineer Caroline Michael.",
    "",
    "3.  Land Acknowledgment Statement",
]
PZ_PAGE = "\n".join(f"{n}{text}" for n, text in enumerate(PZ_LINES, start=1))

SAB_PAGE = """Sustainability Advisory Board  September 16, 2026
1
SUSTAINABILITY ADVISORY BOARD MINUTES
September 16, 2026
This Sustainability Advisory Board Meeting was held remotely.
REGULAR MEETING
1. MEETING CALLED TO ORDER
The September 16, 2026 meeting of the Longmont Sustainability Advisory Board was called to
order by Chair Robert Davidson at 4:30 pm via remote Zoom meeting connection.
ROLL CALL
Board Members Present at Roll Call: Robert Davidson, Korkut Onaran, John Lembke, and Alyssa
Harding.  Alec Solimeo arrived at 4:35 pm.
Board Members Excused: Jeff Gawrych and Daniel Leonard
City Staff Members Present: Francie KirkJaffe, Lisa Knoblauch, Chris Huffer, August Hendrickson,
and Heather McIntyre. Lance Smith, Julie Graf, Brian McGill, and Becky Doyle arrived at 4:33 pm,
and Susan Bartlett arrived at 4:36 pm.
City Council Liaison Present: Alex Kalhofer
2. DETERMINATION OF A QUORUM"""

PACKET = {
    "kind": "packet",
    "url": "https://longmont.primegov.com/Public/CompiledDocument?meetingTemplateId=1",
    "pages": [
        {
            "page": 1,
            "text": "PACKET\nCity Council Regular Session\nNovember 3, 2026\n2.\nROLL CALL",
        },
        {"page": 6, "text": COUNCIL_PAGE},
        {"page": 221, "text": PZ_PAGE},
        {"page": 300, "text": SAB_PAGE},
    ],
}
DAY = date(2026, 11, 3)


def read(body):
    return rollcall.read_documents([PACKET], body, DAY)


def names(reading, status="present"):
    return [s.name for s in reading.sightings if s.status == status]


def test_the_council_roll_call_gives_the_mayor_and_the_six_council_members():
    reading = read("City Council")
    assert names(reading) == [
        "Susie Hidalgo-Fahring",
        "Diane Crist",
        "Alex Kalkhofer",
        "Jake Marsing",
        "Sean McCoy",
        "Matthew Popkin",
        "Crystal Prieto",
    ]
    mayor, crist = reading.sightings[:2]
    assert (mayor.role, crist.role) == ("Mayor", "Council Member")
    assert (mayor.first, mayor.last) == ("Susie", "Hidalgo-Fahring")


def test_each_member_keeps_the_document_page_line_date_and_sentence():
    mayor = read("City Council").sightings[0]
    assert mayor.document == "packet"
    assert mayor.url == PACKET["url"]
    assert mayor.page == 6
    assert mayor.line == 14  # the line the sentence starts on
    assert mayor.held == "2026-10-20"  # the date of the minutes, not of the packet's meeting
    assert mayor.text.startswith("Those present were Mayor Susie Hidalgo-Fahring and Council")


def test_a_roll_call_belongs_to_the_body_whose_minutes_it_sits_in():
    council = {s.name for s in read("City Council").sightings}
    assert "Tom Lange" not in council  # the commission's minutes are in the council's packet
    assert "Robert Davidson" not in council
    board = {s.name for s in read("Sustainability Advisory Board").sightings}
    assert "Diane Crist" not in board


def test_numbered_lines_are_read_and_the_absent_are_members_too():
    reading = read("Planning and Zoning Commission")
    assert names(reading) == [
        "Jacob Arment",
        "Tom Lange",
        "Warren Wang",
        "Michael Polan",
        "Amy Saunders",
        "Ryan Forbes",
        "Andy Jordan",
    ]
    assert names(reading, "absent") == ["Devin Edgley-Wells", "Harrison Earl", "Geri Boone"]
    assert {s.role for s in reading.sightings} == {"Commissioner"}
    assert reading.sightings[0].held == "2026-05-20"
    assert reading.sightings[0].line == 13


def test_a_councils_liaison_to_a_commission_is_not_a_member_of_it():
    reading = read("Planning and Zoning Commission")
    assert "Matthew Popkin" not in {s.name for s in reading.sightings}
    assert reading.liaisons == ["Matthew Popkin"]


def test_staff_and_the_liaison_are_not_members_and_a_late_arrival_is():
    reading = read("Sustainability Advisory Board")
    assert names(reading) == [
        "Robert Davidson",
        "Korkut Onaran",
        "John Lembke",
        "Alyssa Harding",
        "Alec Solimeo",
    ]
    assert names(reading, "absent") == ["Jeff Gawrych", "Daniel Leonard"]
    assert {s.role for s in reading.sightings} == {"Board Member"}
    everyone = {s.name for s in reading.sightings}
    assert not everyone & {"Francie KirkJaffe", "Lance Smith", "Alex Kalhofer", "Susan Bartlett"}


def _one_page(text, body="Planning and Zoning Commission"):
    page = {"kind": "packet", "url": "u", "pages": [{"page": 3, "text": text}]}
    return rollcall.read_documents([page], body, DAY)


def test_a_surname_alone_is_not_a_name_and_is_counted_not_invented():
    reading = _one_page(
        "MINUTES\nPLANNING AND ZONING COMMISSION\nSeptember 16, 2026\n1. Call To Order\n"
        "2. Roll Call\nPresent on the Commission were Devin Edgley-Wells, Tom Lange and "
        "Harrison Earl. Commissioner Amy Saunders and Chair Polan were absent.\n3. Land"
    )
    assert names(reading, "absent") == ["Amy Saunders"]
    assert reading.surname_only == ["Polan"]


def test_an_agenda_roll_call_heading_without_names_gives_nobody():
    reading = _one_page(
        "PACKET\nPlanning and Zoning Commission\nOctober 21, 2026\n1.\nCall to Order\n"
        "2.\nRoll Call\n3.\nNomination"
    )
    assert reading.sightings == []


def test_a_roll_call_that_runs_onto_the_next_page_is_read_whole():
    first = (
        "MINUTES\nCity Council Regular Session\nOctober 6, 2026\n1.\nCALL\n2.\nROLL CALL\n"
        "Those present were Mayor Susie Hidalgo-Fahring and Council Members Diane Crist,"
    )
    second = "Alex Kalkhofer and Sean McCoy.\n3.\nAPPROVAL"
    pages = [{"page": 1, "text": first}, {"page": 2, "text": second}]
    reading = rollcall.read_documents(
        [{"kind": "minutes", "url": "u", "pages": pages}], "City Council", DAY
    )
    assert names(reading) == [
        "Susie Hidalgo-Fahring",
        "Diane Crist",
        "Alex Kalkhofer",
        "Sean McCoy",
    ]


def test_a_member_list_on_the_agenda_is_read_with_its_line():
    agenda = (
        "Source: https://example.test/agenda\nRetrieved 2026-11-03\nAGENDA\n"
        "Mayor Susie Hidalgo-Fahring\nDiane Crist, Council Member\nSean McCoy, Council Member\n"
        "1. CALL TO ORDER\n"
    )
    found = rollcall.read_agenda(agenda, "City Council", DAY)
    assert [(s.name, s.line, s.document, s.url) for s in found] == [
        ("Susie Hidalgo-Fahring", 4, "agenda", "https://example.test/agenda"),
        ("Diane Crist", 5, "agenda", "https://example.test/agenda"),
        ("Sean McCoy", 6, "agenda", "https://example.test/agenda"),
    ]


def test_contact_details_never_reach_a_sighting():
    reading = _one_page(
        "MINUTES\nCity Council Regular Session\nOctober 6, 2026\n1.\nCALL\n2.\nROLL CALL\n"
        "Those present were Mayor Susie Hidalgo-Fahring (susie@example.test, 303-555-0100) and "
        "Council Members Diane Crist and Sean McCoy.\n3.\nAPPROVAL",
        body="City Council",
    )
    assert names(reading) == ["Susie Hidalgo-Fahring", "Diane Crist", "Sean McCoy"]
    text = " ".join(s.text for s in reading.sightings)
    assert "@" not in text
    assert "555" not in text
