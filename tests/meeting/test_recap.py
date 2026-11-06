import pytest

from civiccast_brief.meeting import recap as R
from civiccast_brief.meeting.roster import load_roster
from civiccast_brief.meeting.speakers import Turn
from civiccast_brief.meeting.transcript import Segment

ROSTER = load_roster()


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


def test_speaker_key_lists_named_and_title_only_turns_inside_the_window():
    named = Turn(index=1, first=0, last=0, start=5.0, end=6.0, speaker="Matthew Popkin")
    title = Turn(index=2, first=1, last=1, start=20.0, end=21.0, title="council member")
    later = Turn(index=3, first=5, last=5, start=900.0, end=901.0, speaker="Diane Crist")
    segments = [seg(i * 10.0, f"line {i}") for i in range(6)]
    window = R.windows(segments, 1800)[0]
    key = R.speaker_key([named, title, later], window)
    assert "0:00:05: Matthew Popkin (roll call)" in key
    assert "a council member, name not matched" in key
    assert "Diane Crist" in key  # turn 3 starts at line 5, still in this window


def test_empty_speaker_key_says_none():
    segments = [seg(0.0, "x")]
    window = R.windows(segments, 1800)[0]
    assert R.speaker_key([], window) == "- none in this part"


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
