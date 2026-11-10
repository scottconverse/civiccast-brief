import pytest

from civiccast_brief.meeting import speakers as S
from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster
from civiccast_brief.meeting.transcript import Segment

ROSTER = load_roster(EXAMPLE_ROSTER)


def seg(start, text, turn=False):
    return Segment(start=start, end=start + 1.0, text=text, turn_start=turn)


def test_each_turn_mark_starts_a_turn_and_leading_lines_are_turn_zero():
    segments = [
        seg(0, "opening words"),
        seg(1, "still opening"),
        seg(2, "Council member Popkin", turn=True),
        seg(3, "here.", turn=True),
        seg(4, "more"),
    ]
    turns = S.split_turns(segments)
    assert [(t.first, t.last) for t in turns] == [(0, 1), (2, 2), (3, 4)]
    assert turns[0].speaker is None
    assert turns[0].text == "opening words still opening"


@pytest.mark.parametrize(
    ("text", "last", "first", "mayor", "council"),
    [
        ("Council member Popkin", "popkin", None, False, True),
        ("Mayor Susie Hidalgo-Fahring", "hidalgo-fahring", "susie", True, False),
        ("Mayor Prom McCoy", "mccoy", "prom", True, False),
        ("Mayor Pro Tem McCoy", "mccoy", None, False, False),
        ("Kalkhofer.", "kalkhofer", None, False, False),
    ],
)
def test_short_name_calls_parse(text, last, first, mayor, council):
    call = S.parse_call(text)
    assert call is not None
    assert (call.last, call.first, call.mayor_title, call.council_title) == (
        last,
        first,
        mayor,
        council,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Council member Pto is uh walking in the door.",
        "Thank you, Mayor.",
        "Okay, we are adjourned.",
        "",
    ],
)
def test_sentences_are_not_calls(text):
    assert S.parse_call(text) is None


def test_exact_roster_spelling_is_required():
    # "Christ" and "Crist" differ by one letter; a near match is not a name.
    assert S.match_member(S.parse_call("Council member Christ"), ROSTER) is None
    assert S.match_member(S.parse_call("Council member Crist"), ROSTER).name == "Diane Crist"


def test_garbled_mayor_and_pro_tem_calls_match_nobody():
    assert S.match_member(S.parse_call("Mayor Dgoing"), ROSTER) is None
    # "Mayor Prom McCoy": the first name is wrong and the mayor title does not fit McCoy.
    assert S.match_member(S.parse_call("Mayor Prom McCoy"), ROSTER) is None


def test_first_name_must_agree_when_given():
    assert S.match_member(S.parse_call("Sean Popkin"), ROSTER) is None
    assert S.match_member(S.parse_call("Sean McCoy"), ROSTER).name == "Sean McCoy"


@pytest.mark.parametrize("text", ["present.", "Here.", "Yes", "I'm here", "aye"])
def test_roll_call_answers(text):
    assert S.is_acknowledgment(text)


@pytest.mark.parametrize("text", ["Thank you.", "Here we go with the presentation.", "no"])
def test_other_turns_are_not_answers(text):
    assert not S.is_acknowledgment(text)


def test_matched_call_and_answer_name_the_answer_turn():
    segments = [
        seg(10, "Council member Popkin", turn=True),
        seg(11, "here.", turn=True),
        seg(12, "Thank you, Mayor.", turn=True),
    ]
    turns = S.split_turns(segments)
    report = S.label_roll_call(turns, ROSTER)
    assert turns[1].speaker == "Matthew Popkin"
    assert turns[1].basis == "roll_call"
    assert turns[2].speaker is None
    assert report["calls"][0]["status"] == "named: answer turn"


def test_unmatched_council_call_gives_a_title_but_no_name():
    segments = [
        seg(10, "Council member Christ", turn=True),
        seg(11, "present.", turn=True),
    ]
    turns = S.split_turns(segments)
    S.label_roll_call(turns, ROSTER)
    assert turns[1].speaker is None
    assert turns[1].title == "council member"
    assert turns[1].basis == "roll_call_title_only"


def test_unmatched_mayor_call_gives_nothing():
    segments = [seg(10, "Mayor Dgoing", turn=True), seg(11, "present.", turn=True)]
    turns = S.split_turns(segments)
    report = S.label_roll_call(turns, ROSTER)
    assert turns[1].speaker is None and turns[1].title is None
    assert report["calls"][0]["status"] == "no roster member matches this call"


def test_answer_too_late_is_not_named():
    segments = [seg(10, "Council member Popkin", turn=True), seg(90, "here.", turn=True)]
    turns = S.split_turns(segments)
    report = S.label_roll_call(turns, ROSTER)
    assert turns[1].speaker is None
    assert "more than 20 s" in report["calls"][0]["status"]


def test_a_call_with_no_answer_is_not_named():
    segments = [seg(10, "Council member Popkin", turn=True), seg(11, "Okay, let's go.", turn=True)]
    turns = S.split_turns(segments)
    S.label_roll_call(turns, ROSTER)
    assert turns[1].speaker is None


def test_apply_turns_writes_names_and_unknown():
    segments = [
        seg(10, "Council member Popkin", turn=True),
        seg(11, "here.", turn=True),
        seg(12, "public comment", turn=True),
        seg(13, "more comment"),
    ]
    turns = S.split_turns(segments)
    S.label_roll_call(turns, ROSTER)
    S.apply_turns(segments, turns)
    assert [s.speaker for s in segments] == [
        "unknown",
        "Matthew Popkin",
        "unknown",
        "unknown",
    ]


def test_answer_with_more_words_in_the_same_turn_is_not_named():
    # Without a new turn mark, "here." and the speech after it are one turn, which is not a bare
    # roll-call answer, so nobody is named.
    segments = [
        seg(10, "Council member Popkin", turn=True),
        seg(11, "here.", turn=True),
        seg(12, "a second line of the answer"),
    ]
    turns = S.split_turns(segments)
    S.label_roll_call(turns, ROSTER)
    S.apply_turns(segments, turns)
    assert [s.speaker for s in segments] == ["unknown", "unknown", "unknown"]


def test_speakers_report_counts_named_and_unknown_lines():
    segments = [
        seg(10, "Council member Popkin", turn=True),
        seg(11, "here.", turn=True),
        seg(12, "public comment", turn=True),
        seg(13, "more comment"),
    ]
    turns = S.split_turns(segments)
    roll = S.label_roll_call(turns, ROSTER)
    S.apply_turns(segments, turns)
    report = S.speakers_report("vid", segments, turns, roll)
    assert report["counts"]["segments"] == 4
    assert report["counts"]["named_segments"] == 1
    assert report["counts"]["unknown_segments"] == 3
    assert report["counts"]["named_percent"] == 25.0
    assert report["diarization"]["status"] == "not run"


def test_other_bodies_titles_parse_and_match_in_capitals():
    from civiccast_brief.meeting.roster import roster_from_dict

    board = roster_from_dict(
        {
            "city": "San Mateo County",
            "body": "Board of Supervisors",
            "members": [
                {
                    "name": "Lisa Gauthier",
                    "first": "Lisa",
                    "last": "Gauthier",
                    "role": "Supervisor",
                },
                {
                    "name": "Ray Mueller",
                    "first": "Ray",
                    "last": "Mueller",
                    "role": "Vice President",
                },
            ],
        }
    )
    assert S.match_member(S.parse_call("SUPERVISOR GAUTHIER?"), board).name == "Lisa Gauthier"
    assert S.match_member(S.parse_call("Vice President Mueller"), board).name == "Ray Mueller"
    assert S.parse_call("Supervisor Gauthier is walking in") is None
    assert S.match_member(S.parse_call("SUPERVISOR GOTHIER"), board) is None
