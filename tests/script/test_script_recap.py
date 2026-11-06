import json

import pytest

from civiccast_brief.script.recap import (
    Claim,
    Recap,
    RecapFormatError,
    Transcript,
    load_recap,
    parse_draft_json,
    recap_from_dict,
    save_recap,
    verify_draft,
)

TRANSCRIPT = """Longmont City Council Regular Session - October 20, 2026. YouTube rsagM9xn28Y.
0:05:06  Okay, it is 7 o'clock. I'd like to call
0:05:09  the um city council regular session
0:05:12  >> Mayor Dgoing
0:05:34  >> present.
0:21:14  A resident said the city was allocating
0:21:17  2027 funding for a s
0:21:20  Okay, we are adjourned.
"""


@pytest.fixture
def transcript() -> Transcript:
    return Transcript(TRANSCRIPT)


def draft(**overrides):
    item = {
        "topic": "Meeting",
        "text": "The council met.",
        "timestamp": "0:05:06",
        "quote": "Okay, it is 7 o'clock. I'd like to call",
    }
    item.update(overrides)
    return item


def test_quote_across_caption_lines_is_found(transcript):
    assert transcript.occurrences("it is 7 o'clock. I'd like to call the um city council") == [
        "0:05:06"
    ]


def test_speaker_marks_are_dropped_before_matching(transcript):
    assert transcript.occurrences(">> Mayor Dgoing") == ["0:05:12"]


def test_header_line_is_stamped_zero(transcript):
    assert transcript.occurrences("Longmont City Council Regular Session - October 20, 2026") == [
        "0:00:00"
    ]


def test_quote_must_match_exactly(transcript):
    assert transcript.occurrences("Okay it is seven o'clock") == []
    assert transcript.occurrences("okay, we are adjourned") == []  # case is part of verbatim


def test_verbatim_claim_is_kept_and_renumbered(transcript):
    kept, report = verify_draft(
        [draft(), draft(text="Second.", quote="Okay, we are adjourned.", timestamp="0:21:20")],
        transcript,
    )
    assert [c.id for c in kept] == ["c01", "c02"]
    assert report.kept == 2
    assert report.dropped == []


def test_quote_not_in_transcript_is_dropped(transcript):
    kept, report = verify_draft([draft(quote="The council voted seven to zero.")], transcript)
    assert kept == []
    assert report.dropped[0].reason == "quote is not verbatim in the transcript"


def test_claim_number_missing_from_quote_is_dropped(transcript):
    kept, report = verify_draft(
        [draft(text="The council met on October 6, 2026.", quote="Okay, we are adjourned.")],
        transcript,
    )
    assert kept == []
    assert "number(s) not in the quote" in report.dropped[0].reason


def test_claim_name_missing_from_quote_is_dropped(transcript):
    kept, report = verify_draft(
        [draft(text="The meeting was in Longmont, Colorado.", quote="Okay, it is 7 o'clock.")],
        transcript,
    )
    assert kept == []
    assert "Colorado" in report.dropped[0].reason


def test_wrong_timestamp_is_corrected_to_the_quote_line(transcript):
    kept, report = verify_draft(
        [
            draft(
                text="Public comment began.",
                quote="A resident said the city was",
                timestamp="0:21:50",
            )
        ],
        transcript,
    )
    assert kept[0].timestamp == "0:21:14"
    assert report.corrected_timestamps and "0:21:50 -> 0:21:14" in report.corrected_timestamps[0]


def test_parse_draft_json_ignores_text_around_the_array():
    answer = (
        'Here you go:\n[{"text": "a", "quote": "b", "timestamp": "0:00:01", "topic": "t"}]\nDone.'
    )
    assert parse_draft_json(answer)[0]["text"] == "a"


def test_parse_draft_json_rejects_answer_without_array():
    with pytest.raises(RecapFormatError):
        parse_draft_json('{"text": "not an array"}')


def test_recap_round_trips_through_json(tmp_path):
    recap = Recap(
        city="Longmont",
        body="City Council",
        meeting_date="October 20, 2026",
        video_id="rsagM9xn28Y",
        claims=(Claim("c01", "Meeting", "The council met.", "0:00:00", "Longmont City Council"),),
    )
    path = tmp_path / "recap.json"
    save_recap(recap, path)
    assert load_recap(path) == recap
    assert json.loads(path.read_text(encoding="utf-8"))["claims"][0]["id"] == "c01"


def test_recap_rejects_repeated_ids_and_bad_timestamps():
    base = {"city": "L", "body": "B", "meeting_date": "D", "video_id": "V"}
    repeated = dict(
        base,
        claims=[
            {"id": "c01", "topic": "t", "text": "x", "timestamp": "0:00:00", "quote": "x"},
            {"id": "c01", "topic": "t", "text": "y", "timestamp": "0:00:00", "quote": "y"},
        ],
    )
    with pytest.raises(RecapFormatError, match="repeated"):
        recap_from_dict(repeated)
    bad_time = dict(
        base,
        claims=[
            {"id": "c01", "topic": "t", "text": "x", "timestamp": "5 minutes", "quote": "x"},
        ],
    )
    with pytest.raises(RecapFormatError, match="timestamp"):
        recap_from_dict(bad_time)
