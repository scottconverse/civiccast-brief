"""More speakers named, with the same strict rule: a name is given only for a person on the body's
roster or printed in the meeting's documents. Item 6 of the goal, measured on real runs.

Caption lines are real: Longmont Planning and Zoning Commission, Oct 21, 2026 (unNcPhKJ45o) and
City Council, Oct 27 and Nov 3, 2026 (Fcdk3MPz8Ic, oWZpzjzjwnE). Document lines are real packet
text with the e-mail addresses left out. Each test fails on the code before this change.
"""

from civiccast_brief.meeting import speakers as S
from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster, roster_from_dict
from civiccast_brief.meeting.transcript import Segment

COUNCIL = load_roster(EXAMPLE_ROSTER)


def _member(name: str, role: str = "Board Member") -> dict[str, str]:
    first, last = name.split(" ", 1)
    return {"name": name, "first": first, "last": last, "role": role}


#: The Planning and Zoning Commission roster the city's own portal lists (names only).
COMMISSION = roster_from_dict(
    {
        "city": "Longmont",
        "body": "Planning and Zoning Commission",
        "members": [
            _member("Tom Lange"),
            _member("Geri Boone"),
            _member("Devin Edgley-Wells"),
            _member("Michael Polan", "Chair"),
            _member("Jacob Arment", "Alternate Board Member"),
        ],
    }
)


def lines(*rows):
    """rows: (text, turn_start, voice). One second per line."""
    return [
        Segment(start=float(i), end=float(i) + 1.0, text=text, turn_start=turn, voice=voice)
        for i, (text, turn, voice) in enumerate(rows)
    ]


def named_turns(segments, roster):
    turns = S.split_turns(segments)
    S.label_roll_call(turns, roster)
    report = S.label_recognitions(segments, turns, roster)
    return turns, report["recognitions"]


def printed(*pages):
    data = {"format": 1, "documents": []}
    for kind, page, text in pages:
        data["documents"].append({"kind": kind, "pages": [{"page": page, "text": text}]})
    return S.PrintedNames.from_documents(data)


# --- Roll call: a surname the captioner split in two ---------------------------------------------


def test_roll_call_names_a_hyphenated_surname_the_captioner_split():
    segments = lines(("Commissioner Edgley Wells", True, None), ("here.", True, None))
    turns = S.split_turns(segments)
    S.label_roll_call(turns, COMMISSION)
    assert turns[1].speaker == "Devin Edgley-Wells" and turns[1].basis == "roll_call"


def test_roll_call_still_needs_every_letter_of_the_surname():
    segments = lines(("Commissioner Edgley Wills", True, None), ("here.", True, None))
    turns = S.split_turns(segments)
    S.label_roll_call(turns, COMMISSION)
    assert turns[1].speaker is None


# --- Chair recognitions with the title "Commissioner" -----------------------------------------------


def test_commissioner_hand_off_names_the_next_turn():
    segments = lines(
        ("Okay. Uh, Commissioner Boon.", True, None),
        ("Thank you, Chair. I move to approve the September 16th meeting minutes.", True, None),
    )
    turns, found = named_turns(segments, COMMISSION)
    assert found[0]["matched"] == "Geri Boone" and found[0]["status"] == "named: next turn"
    assert turns[1].speaker == "Geri Boone" and turns[1].basis == "recognition"


def test_a_split_compound_surname_is_a_hand_off_not_a_mention():
    segments = lines(
        ("Go ahead. Um Commissioner Ashley Wells.", True, None),
        ("Thank you. So this question is just a clarifying question for staff.", True, None),
    )
    turns, found = named_turns(segments, COMMISSION)
    assert found[0]["status"] == "named: next turn"
    assert turns[1].speaker == "Devin Edgley-Wells"


def test_commissioner_mentions_and_staff_named_by_title_name_nobody():
    segments = lines(
        ("I would be in agreeance with Commissioner Boon on having not MUE.", True, None),
        ("Thanks.", True, None),
        ("Oh, good evening, Commissioner Don Burchcha, planning manager.", True, None),
        ("Good evening, Commissioners.", True, None),
    )
    turns, _ = named_turns(segments, COMMISSION)
    assert all(t.speaker is None for t in turns)


def test_a_word_that_sounds_like_a_first_name_is_not_a_reading_of_the_member():
    # "Marson" sounds like the chair's first name, Michael. It is not Michael Polan.
    name, _ = S.name_recognition("member", "Marson", COMMISSION)
    assert name is None
    name, _ = S.name_recognition("member", "Lang", COMMISSION)
    assert name == "Tom Lange"


def test_without_voices_a_turn_that_goes_on_after_the_name_does_not_name_the_next_turn():
    # The captioner left out the mark: "Council member Marsen." is the chair, and the words after
    # it are the member's own, so the next turn ("Council member,") is somebody else.
    segments = lines(
        ("Thank you very much,", True, None),
        ("Council member Marsen.", True, None),
        ("Um, I would propose I like the idea of flipping and it seems clear.", False, None),
        ("makes sense.", False, None),
        ("Council member,", True, None),
        ("thank you, Chair. Um,", True, None),
    )
    turns, found = named_turns(segments, COUNCIL)
    assert found[0]["matched"] == "Jake Marsing"
    assert all(t.speaker is None for t in turns)


def test_without_voices_a_sound_note_after_the_name_is_not_a_word():
    segments = lines(
        ("Just a comment. Council member Popkin. [clears throat]", True, None),
        ("Thank you, Mayor. So, back to my last comment.", True, None),
    )
    turns, _ = named_turns(segments, COUNCIL)
    assert turns[1].speaker == "Matthew Popkin"


# --- The chair's voice, not hidden by members' mentions ----------------------------------------------


def test_the_chair_voice_is_found_when_members_mentions_are_many():
    rows = []
    for _ in range(5):  # 5 of 29 recognitions: 17 percent, under the 20 percent bar
        rows += [
            ("Council member Popkin.", True, "voice 01"),
            ("Thank you, Mayor. A comment.", True, "voice 05"),
        ]
    for n in range(10, 34):  # 24 members each agree with Popkin once, from their own seats
        rows += [("I agree with Council Member Popkin.", True, f"voice {n}"), ("Okay.", True, None)]
    segments = lines(*rows)
    turns, found = named_turns(segments, COUNCIL)
    assert sum(1 for f in found if f["status"].startswith("a mention")) == 24
    assert S.chair_voice(segments, turns, found) == "voice 01"


# --- A voice that calls names is not named from them ------------------------------------------------


def test_a_voice_that_calls_several_names_is_not_named_but_the_speaker_called_is():
    # Real lines, Oct 27, 2026: the mayor (voice 01) calls two speakers to the podium.
    segments = lines(
        ("and I think we should keep going.", False, "voice 13"),
        ("Thank you.", False, "voice 01"),
        ("Thank you.", True, "voice 01"),
        ("Jim Harmon.", False, "voice 01"),
        ("Good evening, mayor, members of council.", True, "voice 14"),
        ("I'm Jim Harmon. I'm the managing partner", False, "voice 14"),
        ("of Maple Leaf Investments and a partner", False, "voice 14"),
        ("the emotions.", False, "voice 14"),
        ("Thank you.", False, "voice 01"),
        ("Thank you, uh, Lawrence", False, "voice 01"),
        ("Jacobson.", False, "voice 01"),
        ("Good evening, mayor, council members. My", True, "voice 15"),
        ("name is Lawrence Jacobson, 8350 East", False, "voice 15"),
    )
    names = printed(
        (
            "packet",
            22,
            "MEMORANDUM TO: Harold Dominguez, City Manager From: Jim Harmon/Larry Jacobson",
        )
    )
    turns = S.split_turns(segments)
    S.apply_turns(segments, turns)
    S.label_voices(segments, turns, [], COUNCIL, names)
    by_voice = {s.voice: s.speaker for s in segments}
    assert by_voice["voice 14"] == "Jim Harmon"
    assert by_voice["voice 01"] == S.UNKNOWN


# --- Staff the documents print, spelled by ear in the captions ------------------------------------


STAFF = (
    "packet",
    38,
    (
        "BY: Lynn Yarmey, Office of Data & Analytics Ana Castro, Office of Data & Analytics "
        "EXECUTIVE SUMMARY: The 2026 Community Satisfaction Survey"
    ),
)


def _intro(spoken_lines, names, voice="voice 06"):
    segments = lines(*[(t, i == 0, voice) for i, t in enumerate(spoken_lines)])
    turns = S.split_turns(segments)
    S.apply_turns(segments, turns)
    report = S.label_voices(segments, turns, [], COUNCIL, names)
    return segments[0].speaker, report["introductions"]


def test_a_staff_name_spelled_by_ear_is_named_as_the_documents_print_it():
    # Real lines, Nov 3, 2026: the captioner wrote "Anna"; the packet prints "Ana Castro".
    speaker, intros = _intro(
        ["Hello. Um, I'm Anna Castro, data analy", "uh data analyst for the um strategic"],
        printed(STAFF),
    )
    assert speaker == "Ana Castro"
    assert intros[0]["printed_in"] == "packet p. 38" and intros[0]["matched_by"] == "sound"


def test_a_name_that_sounds_like_two_printed_names_names_nobody():
    two = printed(STAFF, ("minutes", 3, "Public comment: Anne Castro of Main Street spoke."))
    speaker, _ = _intro(["Hello. Um, I'm Anna Castro, data analy", "uh data analyst"], two)
    assert speaker == S.UNKNOWN


def test_a_name_that_does_not_sound_like_a_printed_name_names_nobody():
    speaker, intros = _intro(["Hello. Um, I'm Anna Costa, data analy", "uh data"], printed(STAFF))
    assert speaker == S.UNKNOWN and "not printed" in intros[0]["status"]
