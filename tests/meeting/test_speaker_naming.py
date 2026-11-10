"""Speaker names beyond the roll call: chair recognitions read by sound, then voices.

Each test fails on the roll-call-only code (before November 10, 2026), which named one line
per meeting and left every recognized member "unknown".
"""

from civiccast_brief.meeting import speakers as S
from civiccast_brief.meeting.recap import speaker_key, windows
from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster
from civiccast_brief.meeting.transcript import Segment

ROSTER = load_roster(EXAMPLE_ROSTER)


def lines(*rows):
    """rows: (text, turn_start, voice). One second per line."""
    return [
        Segment(start=float(i), end=float(i) + 1.0, text=text, turn_start=turn, voice=voice)
        for i, (text, turn, voice) in enumerate(rows)
    ]


def test_a_misheard_recognition_names_the_next_turn():
    segments = lines(("Council member Marsen.", False, None), ("Thank you, Mayor.", True, None))
    turns = S.split_turns(segments)
    found = S.label_recognitions(segments, turns, ROSTER)["recognitions"]
    assert found[0]["matched"] == "Jake Marsing"
    assert turns[1].speaker == "Jake Marsing" and turns[1].basis == "recognition"


def test_a_glued_pro_tem_is_never_read_as_the_mayor():
    # "Mayor Pro Tem McCoy" captioned as "Mayor Promcoy" once sounded closest to the mayor.
    name, _ = S.name_recognition("mayor", "Promcoy", ROSTER)
    assert name != "Susie Hidalgo-Fahring"


def test_a_name_heard_with_no_close_roster_match_names_nobody():
    name, match = S.name_recognition("member", "Anderson", ROSTER)
    assert name is None and match is not None and match.score < 0.72


def test_a_mention_does_not_name_the_next_turn():
    segments = lines(
        ("And that carries six to one with Council Member Crist in opposition.", False, None),
        ("Next item is the budget.", True, None),
        ("I think Council Member Popkin said it well.", True, None),
        ("Staff can answer that.", True, None),
    )
    turns = S.split_turns(segments)
    found = S.label_recognitions(segments, turns, ROSTER)["recognitions"]
    assert [f["status"] for f in found] == ["a mention, not a hand-off"] * 2
    assert all(t.speaker is None for t in turns)


def test_hand_offs_that_end_the_line_or_ask_a_question_name_the_next_turn():
    assert S.is_hand_off("Council member Popkin, go ahead.", "Popkin go ahead")
    assert S.is_hand_off("Um council member Marcen, do you have a comment?", "Marcen do you")
    assert not S.is_hand_off("seconded by Council Member Popkin.", "Popkin")
    assert not S.is_hand_off("Council member Marcen made the motion.", "Marcen made the")


def test_the_roll_call_name_wins_over_a_recognition():
    segments = lines(
        ("Council member Popkin", False, None),
        ("here.", True, None),
    )
    turns = S.split_turns(segments)
    S.label_roll_call(turns, ROSTER)
    found = S.label_recognitions(segments, turns, ROSTER)["recognitions"]
    assert turns[1].speaker == "Matthew Popkin"
    assert all(f["status"] != "named: next turn" for f in found)


def _meeting():
    """A chair (voice 01) hands the floor to Crist twice (voice 05), and a staff member
    (voice 09) presents, then answers inside Crist's second turn without a new turn mark.
    Crist speaks once more later with no recognition before it."""
    return lines(
        ("Council member Christ.", False, "voice 01"),  # 0 chair
        ("Thank you, Mayor. I have a question.", True, "voice 05"),  # 1 Crist
        ("About the budget.", False, "voice 05"),  # 2
        ("Staff presentation begins.", True, "voice 09"),  # 3 staff
        ("More of the presentation.", False, "voice 09"),  # 4
        ("Still presenting.", False, "voice 09"),  # 5
        ("Council member Crist.", True, "voice 01"),  # 6 chair
        ("Thanks. One more question.", True, "voice 05"),  # 7 Crist
        ("The staff answer, same caption turn.", False, "voice 09"),  # 8 staff
        ("Okay, next item.", True, "voice 01"),  # 9 chair
        ("I support this.", True, "voice 05"),  # 10 Crist, not recognized
    )


def test_voices_name_unrecognized_turns_and_keep_chair_and_staff_unknown():
    segments = _meeting()
    turns = S.split_turns(segments)
    S.label_roll_call(turns, ROSTER)
    found = S.label_recognitions(segments, turns, ROSTER)["recognitions"]
    S.apply_turns(segments, turns)
    report = S.label_voices(segments, turns, found)
    names = [s.speaker for s in segments]
    assert names[1] == names[2] == names[7] == "Diane Crist"
    assert names[10] == "Diane Crist"  # named by voice: two named turns agree
    assert names[8] == "unknown"  # staff voice inside Crist's turn
    assert names[0] == names[6] == names[9] == "unknown"  # the chair's own lines
    assert names[3] == names[4] == names[5] == "unknown"
    assert report["lines_named_by_voice"] == 1
    assert {v["voice"]: v["name"] for v in report["voices"]}["voice 05"] == "Diane Crist"


def test_report_counts_lines_not_turns_and_the_key_says_how_each_name_was_found():
    segments = _meeting()
    turns = S.split_turns(segments)
    roll = S.label_roll_call(turns, ROSTER)
    rec = S.label_recognitions(segments, turns, ROSTER)
    S.apply_turns(segments, turns)
    voices = S.label_voices(segments, turns, rec["recognitions"])
    report = S.speakers_report("v", segments, turns, roll, {"status": "run"}, rec, voices)
    assert report["counts"]["named_segments"] == 4  # lines 1, 2, 7, 10; not line 8
    key = speaker_key(segments, turns, windows(segments)[0], ROSTER)
    assert "Diane Crist, ward 1 representative (the chair gave them the floor)" in key
    assert "Diane Crist, ward 1 representative (the same voice as their other named turns)" in key
