import pytest

from civiccast_brief.meeting import transcript as T


def test_turn_marks_are_removed_and_flagged():
    assert T.normalize_text(">> Mayor  Dgoing") == ("Mayor Dgoing", True)
    assert T.normalize_text("  plain   words ") == ("plain words", False)
    assert T.normalize_text("thanks >> okay") == ("thanks okay", True)


def test_quote_normalization_drops_turn_marks_and_extra_spaces():
    assert T.normalize_quote(">> I pledge   allegiance") == "I pledge allegiance"


def test_empty_caption_lines_are_dropped_and_order_kept():
    items = [
        {"start_ms": 1500, "end_ms": 3000, "text": "first"},
        {"start_ms": 3000, "end_ms": 3100, "text": ">>"},
        {"start_ms": 4000, "end_ms": 6250, "text": ">> second line"},
    ]
    segments = T.segments_from_capture(items)
    assert [s.text for s in segments] == ["first", "second line"]
    assert [s.start for s in segments] == [1.5, 4.0]
    assert [s.end for s in segments] == [3.0, 6.25]
    assert [s.turn_start for s in segments] == [False, True]
    assert all(s.speaker is None for s in segments)


def test_end_is_never_before_start():
    segments = T.segments_from_capture([{"start_ms": 5000, "end_ms": 4000, "text": "x"}])
    assert segments[0].end == segments[0].start == 5.0


@pytest.mark.parametrize(
    ("seconds", "stamp"),
    [(0, "0:00:00"), (36.9, "0:00:36"), (3599.9, "0:59:59"), (20113, "5:35:13")],
)
def test_hms_formats_hours_unpadded(seconds, stamp):
    assert T.hms(seconds) == stamp
    assert T.seconds_of(stamp) == int(seconds)


def test_seconds_of_rejects_other_forms():
    with pytest.raises(ValueError):
        T.seconds_of("05:06")


@pytest.mark.parametrize(
    ("title", "date"),
    [
        ("City Council Regular Session - 20 October 2026", "October 20, 2026"),
        ("City Council Regular Session 06/23/26", "June 23, 2026"),
        ("City Council Regular Session - 08/25/2026", "August 25, 2026"),
        ("Planning and Zoning Commission 9/16/26", "September 16, 2026"),
        # Longmont's two channels, 2026-10-21: the weekly run found these and this step stopped.
        ("Sustainability Advisory Board Meeting Oct. 21, 2026", "October 21, 2026"),
        ("Sustainability Advisory Board - October 21, 2026", "October 21, 2026"),
        ("Town Hall about parking", None),
    ],
)
def test_meeting_date_is_read_from_the_title(title, date):
    assert T.meeting_date_from_title(title) == date


def test_json_round_trip_keeps_fields():
    segments = [T.Segment(start=1.0, end=2.5, text="hello", turn_start=True, speaker="unknown")]
    data = T.to_json(
        segments,
        video_id="abc",
        title="t",
        meeting_date="October 20, 2026",
        source={"platform": "youtube"},
    )
    assert data["segment_count"] == 1
    assert data["segments"][0] == {
        "start": 1.0,
        "end": 2.5,
        "text": "hello",
        "turn_start": True,
        "voice": None,
        "speaker": "unknown",
    }
    assert T.from_json(data) == segments
