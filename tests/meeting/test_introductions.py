"""A voice named from its own self-introduction, only when the meeting's documents print the name.

The caption lines and document lines are real, from Longmont City Council meetings: Oct 6, 2026
(nN9cxsQngms, packet p. 88 and the draft minutes p. 10) and Oct 27, 2026 (Fcdk3MPz8Ic). Before
this change every introduction was listed for the editor and named nobody.
"""

import json

from civiccast_brief.meeting import speakers as S
from civiccast_brief.meeting.recap import speaker_key, windows
from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster
from civiccast_brief.meeting.transcript import Segment

ROSTER = load_roster(EXAMPLE_ROSTER)

MINUTES_P10 = (
    "Mayor\xa0Hidalgo-Fahring\xa0read\xa0the\xa0proclamation\xa0into\xa0the\xa0record\xa0and\xa0"
    "invited\nMorgan\xa0Lundy\xa0from\xa0Community\xa0and\xa0Neighborhood\xa0Resources\xa0to"
    "\xa0accept\xa0the\nproclamation"
)
PACKET_P88 = (
    "18Other staff present:  Senior Planner Kristin Cote, Planning Manager Don Burchett, "
    "Assistant Fire \n19Marshal Marc Sampson, PWNR Engineering Administrator Josh Sherman, "
    "Civil Engineer \n20Caroline Michael"
)


def lines(*rows, start=0.0):
    """rows: (text, turn_start, voice), two seconds apart."""
    return [
        Segment(start=start + 2.0 * i, end=start + 2.0 * i + 1.5, text=t, turn_start=turn, voice=v)
        for i, (t, turn, v) in enumerate(rows)
    ]


def printed(*pages):
    """Document pages as documents.json holds them: (kind, page, text)."""
    data = {"format": 1, "documents": []}
    for kind, page, text in pages:
        data["documents"].append({"kind": kind, "url": f"https://x.test/{kind}", "pages": []})
        data["documents"][-1]["pages"].append({"page": page, "text": text})
    return S.PrintedNames.from_documents(data)


def label(segments, names=None):
    turns = S.split_turns(segments)
    S.label_roll_call(turns, ROSTER)
    found = S.label_recognitions(segments, turns, ROSTER)["recognitions"]
    S.apply_turns(segments, turns)
    return turns, S.label_voices(segments, turns, found, ROSTER, printed=names)


def test_my_name_is_names_the_voice_when_the_minutes_print_the_name():
    segments = lines(
        ("Hi, thank you, Mayor Hildalgo Fairing.", True, "voice 09"),
        ("Um, I my name is Morgan Lundy. I work", False, "voice 09"),
        ("for the city of Longmont and community", False, "voice 09"),
        ("neighbor and neighborhood resources as a", False, "voice 09"),
    )
    turns, report = label(segments, printed(("minutes", 10, MINUTES_P10)))
    assert {s.speaker for s in segments} == {"Morgan Lundy"}
    [intro] = report["introductions"]
    assert intro["name"] == "Morgan Lundy" and intro["printed_in"] == "minutes p. 10"
    assert intro["status"] == "named"
    key = speaker_key(segments, turns, windows(segments)[0], ROSTER)
    assert "Morgan Lundy (introduced themselves; the name is printed in minutes p. 10)" in key
    # No role is given: the key never adds one the documents were not read for.
    assert "Morgan Lundy," not in key


def test_a_name_after_the_greeting_names_the_voice_when_the_packet_prints_it():
    segments = lines(
        ("[clears throat]", True, "voice 29"),
        ("Uh, good evening, council, council", False, "voice 29"),
        ("member Popkin, mayor. Uh, Josh Sherman,", False, "voice 29"),
        ("civil engineer with public works.", False, "voice 29"),
        ("um following up um from a couple weeks", False, "voice 29"),
    )
    _turns, report = label(segments, printed(("packet", 88, PACKET_P88)))
    assert {s.speaker for s in segments} == {"Josh Sherman"}
    assert report["introductions"][0]["printed_in"] == "packet p. 88"


def test_a_name_the_documents_do_not_print_stays_unknown():
    segments = lines(
        ("Thank you. Thanks.", True, "voice 06"),
        ("My name is Naomi Kurland and I live at", False, "voice 06"),
        ("2073 Goldfinch Court in Longmont and I'm", False, "voice 06"),
    )
    _turns, report = label(segments, printed(("packet", 88, PACKET_P88)))
    assert {s.speaker for s in segments} == {"unknown"}
    [intro] = report["introductions"]
    assert intro["spoken"] == "Naomi Kurland" and intro["name"] is None
    assert intro["status"] == "not named: the name is not printed in the meeting's documents"


def test_a_voice_that_names_someone_else_is_not_named_but_the_one_who_says_i_am_is():
    lance = ("packet", 31, "Lance Smith, Director of Business Enablement")
    jen = ("packet", 5, "Jen Uten, Assistant City Manager")
    segments = lines(
        ("And I would like to", False, "voice 28"),
        ("invite Jen Uten, assistant city manager", False, "voice 28"),
        ("to the podium. And she's already there.", False, "voice 28"),
        ("Like, wait, it's Lance Smith. You are", True, "voice 01"),
        ("not Lance Smith. I'm", False, "voice 01"),
        ("not.", True, "voice 01"),
        ("Okay. So, [laughter]", True, "voice 32"),
        ("Good evening, mayor and city council. Uh", True, "voice 23"),
        ("I am Lance Smith. Um so [laughter]", False, "voice 23"),
        ("uh the director of business enablement.", False, "voice 23"),
    )
    _turns, report = label(segments, printed(lance, jen))
    by_voice = {s.voice: s.speaker for s in segments}
    assert by_voice == {
        "voice 28": "unknown",
        "voice 01": "unknown",
        "voice 32": "unknown",
        "voice 23": "Lance Smith",
    }
    assert [i["voice"] for i in report["introductions"]] == ["voice 23"]


def test_a_voice_that_introduces_two_printed_names_is_not_named():
    pages = printed(("packet", 88, PACKET_P88))
    segments = lines(
        ("My name is Josh Sherman.", True, "voice 40"),
        ("And I'm Kristin Cote, senior planner.", True, "voice 40"),
    )
    _turns, report = label(segments, pages)
    assert {s.speaker for s in segments} == {"unknown"}
    assert {i["status"] for i in report["introductions"]} == {
        "not named: this voice also introduces another name"
    }


def test_the_chair_and_roster_members_keep_their_own_rules():
    # The roster's mayor answering "my name is" does not get a second, document-made name.
    pages = printed(("packet", 1, "Susie Hidalgo-Fahring, Mayor"))
    segments = lines(("My name is Susie Hidalgo-Fahring.", True, "voice 03"))
    _turns, report = label(segments, pages)
    assert segments[0].speaker == "unknown"
    assert report["introductions"][0]["status"] == (
        "not named: a roster member is named by the roll call and recognitions"
    )


def test_without_documents_an_introduction_names_nobody():
    segments = lines(("Um, I my name is Morgan Lundy. I work", True, "voice 09"))
    _turns, report = label(segments)
    assert segments[0].speaker == "unknown"
    assert report["introductions"][0]["status"] == "not named: no documents were given"


def test_agenda_text_counts_as_a_document():
    names = S.PrintedNames.from_documents(None, agenda="9.A Presentation by Morgan Lundy")
    assert names.find("Morgan Lundy") == "agenda"
    assert names.find("morgan lundy") == "agenda"
    assert names.find("Morgan") is None  # one word is never enough
    assert names.find("Lundy Morgan") is None


def test_documents_json_file_loads(tmp_path):
    path = tmp_path / "documents.json"
    data = {
        "format": 1,
        "documents": [{"kind": "minutes", "pages": [{"page": 10, "text": MINUTES_P10}]}],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    assert S.PrintedNames.load(path).find("Morgan Lundy") == "minutes p. 10"


def test_a_short_answer_that_is_a_printed_place_is_not_a_name():
    # Oct 20, 2026 at 2:19:42: a member answers a question with a place the packet prints.
    pages = printed(("packet", 594, "South Main, Station Area Plan"))
    segments = lines(
        ("Yeah, you would be looking at it from", True, "voice 08"),
        ("South Main.", False, "voice 04"),
        ("Yeah, correct. That's that's", False, "voice 04"),
    )
    _turns, report = label(segments, pages)
    assert {s.speaker for s in segments} == {"unknown"}
    assert report["introductions"] == []


def test_a_name_in_a_pdf_table_ends_at_the_next_title():
    # Nov 3, 2026 packet p. 359: "DEVELOPER: ACTIS, LLC Mr. Chris Neils Mr. Frank DeSiena".
    names = S.PrintedNames(
        [("packet p. 359", "DEVELOPER: ACTIS, LLC Mr. Chris Neils Mr. Frank DeSiena")]
    )
    assert names.find("Chris Neils") == "packet p. 359"
    # A longer printed name is not the spoken one: "Nathan Jones-Meyer", "Mary Jane Voogt".
    names = S.PrintedNames([("minutes p. 20", "Nathan Jones-Meyer, Planner; Mary Jane Voogt")])
    assert names.find("Nathan Jones") is None
    assert names.find("Mary Jane") is None


def test_addressing_the_council_is_a_greeting_before_the_name():
    # Sep 22, 2026 at 2:33:35; the packet p. 466 prints her name.
    pages = printed(("packet", 466, "Susan Bartlett, Director of Energy Strategies and Solutions"))
    segments = lines(
        ("Mhm.", True, "voice 14"),
        ("Mayor, council members, Susan Bartlett,", True, "voice 34"),
        ("director of energy strategies and", False, "voice 34"),
    )
    label(segments, pages)
    assert [s.speaker for s in segments[1:]] == ["Susan Bartlett", "Susan Bartlett"]
