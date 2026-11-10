import pytest

from civiccast_brief.meeting import recap as R
from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster
from civiccast_brief.meeting.speakers import Turn
from civiccast_brief.meeting.transcript import Segment

ROSTER = load_roster(EXAMPLE_ROSTER)


def seg(start, text, turn=False, speaker=None):
    return Segment(start=start, end=start + 1.0, text=text, turn_start=turn, speaker=speaker)


def test_windows_cut_by_start_time_and_keep_whole_lines():
    segments = [
        seg(0, "a"),
        seg(1799, "b"),
        seg(1800, "c"),
        seg(3700, "d"),
    ]
    windows = R.windows(segments, 1800)
    assert [(w.index, w.first, w.last) for w in windows] == [(1, 0, 1), (2, 2, 2), (3, 3, 3)]
    assert windows[0].start == 0 and windows[0].end == 1800.0


def test_transcript_lines_show_turn_marks_and_timestamps():
    segments = [seg(5.0, "Okay", turn=True), seg(65.0, "next line")]
    window = R.windows(segments, 1800)[0]
    assert R.transcript_lines(segments, window) == "0:00:05  >> Okay\n0:01:05  next line"


def test_transcript_lines_show_the_speaker_of_each_named_line():
    segments = [
        seg(5.0, "Thank you, Mayor.", turn=True, speaker="Diane Crist"),
        seg(6.0, "staff answer", speaker="unknown"),
    ]
    window = R.windows(segments, 1800)[0]
    assert R.transcript_lines(segments, window) == (
        "0:00:05  >> [Diane Crist] Thank you, Mayor.\n0:00:06  staff answer"
    )


def test_speaker_key_names_each_line_run_and_how_it_was_named():
    # Turn 1 was named by a recognition. Its last line is in another voice and stays unknown.
    # Turn 2 was not named, but its voice was, so its lines carry the name with basis "voice".
    named = Turn(index=1, first=0, last=2, start=0.0, end=21.0, speaker="Diane Crist")
    named.basis = "recognition"
    title = Turn(index=2, first=3, last=3, start=30.0, end=31.0, title="council member")
    by_voice = Turn(index=3, first=4, last=5, start=40.0, end=51.0)
    segments = [seg(i * 10.0, f"line {i}") for i in range(6)]
    for i, name in enumerate(
        ["Diane Crist", "Diane Crist", "unknown", "unknown", "Matthew Popkin", "Matthew Popkin"]
    ):
        segments[i].speaker = name
    window = R.windows(segments, 1800)[0]
    key = R.speaker_key(segments, [named, title, by_voice], window, ROSTER).splitlines()
    assert key[0].startswith("- 0:00:00 to 0:00:11: Diane Crist")
    assert key[0].endswith("(the chair gave them the floor)")
    assert key[1] == "- 0:00:30: a council member, name not matched (roll call)"
    assert key[2].startswith("- 0:00:40 to 0:00:51: Matthew Popkin")
    assert key[2].endswith("(the same voice as their other named turns)")
    assert len(key) == 3


def test_empty_speaker_key_says_none():
    segments = [seg(0.0, "x")]
    window = R.windows(segments, 1800)[0]
    assert R.speaker_key(segments, [], window) == "- none in this part"


def test_prompt_carries_agenda_roster_speaker_key_and_lines():
    segments = [seg(5.0, "Council member Popkin", turn=True), seg(6.0, "here.", turn=True)]
    turns = []
    window = R.windows(segments, 1800)[0]
    prompt = R.build_prompt(
        title="City Council Regular Session",
        meeting_date="October 20, 2026",
        part=1,
        parts=1,
        window=window,
        segments=segments,
        turns=turns,
        roster=ROSTER,
        agenda_text="AGENDA\nGolf Course Advisory Board",
    )
    assert "Golf Course Advisory Board" in prompt
    assert "Susie Hidalgo-Fahring (Mayor)" in prompt
    assert "0:00:05  >> Council member Popkin" in prompt
    assert "Part 1 of 1" in prompt
    assert "At most 12 items" in prompt


def test_prompt_without_agenda_says_so():
    segments = [seg(0.0, "x")]
    window = R.windows(segments, 1800)[0]
    prompt = R.build_prompt(
        title="t",
        meeting_date="d",
        part=1,
        parts=1,
        window=window,
        segments=segments,
        turns=[],
        roster=ROSTER,
        agenda_text="",
    )
    assert "no agenda file was given" in prompt


def test_parse_items_ignores_text_around_the_array():
    answer = (
        'Here:\n```json\n[{"topic": "t", "text": "a", "timestamp": "0:00:01", "quote": "b"}]\n```'
    )
    assert R.parse_items(answer) == [
        {"topic": "t", "text": "a", "timestamp": "0:00:01", "quote": "b"}
    ]


@pytest.mark.parametrize("answer", ["no array here", "[not json", '{"a": 1}'])
def test_parse_items_rejects_bad_answers(answer):
    with pytest.raises(R.DraftFormatError):
        R.parse_items(answer)


def test_draft_window_records_a_format_error_without_raising():
    class Receipt:
        def line(self):
            return "[receipt] in=1 out=2"

    def complete(prompt):
        return ("sorry, no list", Receipt())

    draft = R.draft_window(complete, "prompt", 3)
    assert draft.window == 3 and draft.items == []
    assert draft.error and draft.receipt == "[receipt] in=1 out=2"


def test_draft_window_keeps_items_from_a_good_answer():
    def complete(prompt):
        return ('[{"topic": "t", "text": "x", "timestamp": "0:00:01", "quote": "y"}]', object())

    draft = R.draft_window(complete, "prompt", 1)
    assert draft.error is None
    assert draft.items[0]["quote"] == "y"
