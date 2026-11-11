import pytest

from civiccast_brief.meeting import agenda as A
from civiccast_brief.meeting import verify as V
from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster
from civiccast_brief.meeting.transcript import Segment

ROSTER = load_roster(EXAMPLE_ROSTER)
VOCAB = frozenset({"golf", "course", "advisory", "board", "vrain", "8513", "annexation"})


def seg(start, text, speaker=None, turn=False):
    return Segment(start=start, end=start + 1.0, text=text, turn_start=turn, speaker=speaker)


SEGMENTS = [
    seg(5.0, "Okay, it is 7 o'clock. I'd like to call"),
    seg(8.0, "the um city council regular session", turn=True),
    seg(12.0, "Council member Popkin", speaker=None, turn=True),
    seg(14.0, "here.", speaker="Matthew Popkin", turn=True),
    seg(70.0, "The public hearing is closed. Seven to zero vote."),
    seg(80.0, "This is the Golf Course Advisory Board item.", turn=True),
    seg(200.0, "Okay, we are adjourned."),
]


def item(text, quote, timestamp, topic="Topic"):
    return {"topic": topic, "text": text, "timestamp": timestamp, "quote": quote}


def check(draft, segments=SEGMENTS, vocabulary=VOCAB):
    index = V.TranscriptIndex(segments)
    return V.verify(
        draft,
        index,
        segments,
        ROSTER,
        vocabulary,
        speaker_at=lambda position: segments[position].speaker,
    )


def test_quote_across_caption_lines_is_kept_and_stamped_where_it_starts():
    claims, report = check(
        [item("The council met.", "Okay, it is 7 o'clock. I'd like to call", "0:00:05")]
    )
    assert [c.id for c in claims] == ["c01"]
    assert claims[0].timestamp == "0:00:05"
    assert report["kept"] == 1 and report["dropped"] == 0


def test_speaker_turn_marks_are_not_part_of_a_quote():
    claims, _ = check([item("The answer was given.", ">> here.", "0:00:14")])
    assert len(claims) == 1
    assert claims[0].quote == "here."


def test_a_speaker_name_tag_copied_from_the_prompt_is_not_part_of_a_quote():
    claims, _ = check(
        [item("An answer was given.", "[Matthew Popkin] here. The public hearing", "0:00:14")]
    )
    assert len(claims) == 1
    assert claims[0].quote == "here. The public hearing"


def test_a_bracket_in_the_captions_that_is_not_a_speaker_name_stays_in_the_quote():
    segments = [*SEGMENTS[:6], seg(90.0, "[laughter] That is all."), SEGMENTS[6]]
    kept, _ = check([item("People laughed.", "[laughter] That is all.", "0:01:30")], segments)
    assert len(kept) == 1
    dropped, _ = check([item("That was all.", "[Diane Crist] That is all.", "0:01:30")], segments)
    assert dropped == []


def test_a_role_the_quote_and_speaker_key_do_not_give_is_dropped():
    # The line at 0:00:14 is named Matthew Popkin, not a council member title, and the quote does
    # not say "council member".
    claims, report = check([item("A council member answered.", ">> here.", "0:00:14")])
    assert claims == []
    assert "council member" in report["dropped_items"][0]["reason"]


def test_a_role_backed_by_the_council_title_in_the_speaker_key_is_kept():
    segments = [seg(1.0, "here.", speaker="unknown")]
    index = V.TranscriptIndex(segments)
    claims, _ = V.verify(
        [item("A council member answered.", "here.", "0:00:01")],
        index,
        segments,
        ROSTER,
        VOCAB,
        speaker_at=lambda position: segments[position].speaker,
        title_at=lambda position: "council member",
    )
    assert [c.text for c in claims] == ["A council member answered."]


def test_timestamp_is_corrected_to_the_quote_line_and_reported():
    claims, report = check(
        [item("The public hearing closed.", "The public hearing is closed.", "0:01:05")]
    )
    assert claims[0].timestamp == "0:01:10"
    assert report["timestamp_corrections"] == ["The public hearing closed. 0:01:05 -> 0:01:10"]


def test_quote_not_in_the_transcript_is_dropped():
    claims, report = check([item("The vote was seven to zero.", "Seven to nothing", "0:01:10")])
    assert claims == []
    assert report["drop_reasons"] == {"quote is not verbatim in the transcript": 1}


def test_quote_must_match_case_and_punctuation():
    claims, _ = check([item("The council adjourned.", "okay, we are adjourned", "0:03:20")])
    assert claims == []


def test_quote_far_from_the_cited_time_is_dropped():
    claims, report = check([item("The meeting closed.", "Okay, we are adjourned.", "0:00:05")])
    assert claims == []
    assert "too far from the cited time" in report["dropped_items"][0]["reason"]


def test_number_missing_from_the_quote_is_dropped():
    claims, report = check([item("The vote was 7-0.", "The public hearing is closed.", "0:01:10")])
    assert claims == []
    assert "number not in the quote" in report["dropped_items"][0]["reason"]


def test_number_word_in_the_quote_counts_as_the_digit():
    claims, _ = check([item("The vote was 7-0.", "Seven to zero vote.", "0:01:10")])
    assert [c.text for c in claims] == ["The vote was 7-0."]


def test_named_person_is_kept_when_the_speaker_label_is_that_person():
    claims, _ = check([item("Matthew Popkin answered.", "here.", "0:00:14")])
    assert [c.text for c in claims] == ["Matthew Popkin answered."]


def test_named_person_is_dropped_when_nothing_backs_the_name():
    claims, report = check([item("Sean McCoy spoke.", "Okay, we are adjourned.", "0:03:20")])
    assert claims == []
    assert "Sean McCoy" in report["dropped_items"][0]["reason"]


def test_person_named_by_last_name_in_the_quote_is_backed():
    segments = [seg(1.0, "Popkin, go ahead.")]
    claims, _ = check([item("Popkin asked a question.", "Popkin, go ahead.", "0:00:01")], segments)
    assert len(claims) == 1


def test_a_titled_name_not_on_the_roster_is_dropped():
    claims, report = check(
        [item("Council Member Calcoffer spoke.", "Okay, we are adjourned.", "0:03:20")]
    )
    assert claims == []
    assert "Calcoffer" in report["dropped_items"][0]["reason"]


def test_a_misheard_board_name_is_dropped_and_the_agenda_spelling_is_kept():
    misheard, _ = check(
        [item("The Gulf Course Advisory Board met.", "Okay, we are adjourned.", "0:03:20")]
    )
    assert misheard == []
    official, _ = check(
        [item("The Golf Course Advisory Board met.", "Okay, we are adjourned.", "0:03:20")]
    )
    assert len(official) == 1


def test_claims_are_numbered_in_time_order():
    claims, _ = check(
        [
            item("Late claim.", "Okay, we are adjourned.", "0:03:20"),
            item("Early claim.", "Okay, it is 7 o'clock.", "0:00:05"),
        ]
    )
    assert [(c.id, c.text) for c in claims] == [
        ("c01", "Early claim."),
        ("c02", "Late claim."),
    ]


def test_items_missing_fields_are_dropped_with_a_reason():
    claims, report = check([{"text": "no quote here", "timestamp": "0:00:05"}])
    assert claims == []
    assert report["dropped_items"][0]["reason"] == "missing text, quote or timestamp"


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("7-0", {"7", "0"}),
        ("seven to zero", {"7", "0"}),
        ("$200,000", {"200000"}),
        ("two hundred thousand dollars", {"200000"}),
        ("October 20, 2026", {"20", "2026"}),
        ("twenty-six", {"26"}),
        ("no numbers here", set()),
    ],
)
def test_numbers_are_read_as_digits(quote, expected):
    assert V.numbers_in(quote) == expected


def test_agenda_loader_drops_the_page_header_and_builds_vocabulary(tmp_path):
    path = tmp_path / "agenda.txt"
    path.write_text(
        "Source: https://example.test/agenda\n"
        "Retrieved 2026-11-05 (public page).\n\n"
        "Meeting\nSubscribe\nAGENDA\nGolf Course Advisory Board\n",
        encoding="utf-8",
    )
    agenda = A.load_agenda(path)
    assert agenda.source == "https://example.test/agenda"
    assert agenda.text.startswith("AGENDA")
    assert "golf" in agenda.vocabulary and "subscribe" not in agenda.vocabulary
    assert A.load_agenda(None) is None


# Sep 22, 2026 (jhsFsEz0P5A) at 3:02:49: voice 33 introduced herself as Becky Doyle, a name the
# packet prints, so the speaker label names her. Her name is not an agenda or roster word.
DOYLE = [
    seg(10969.0, "uh to to recognize that change and we", speaker="Becky Doyle", turn=True),
    seg(10971.0, "will be changing the name of the fund to", speaker="Becky Doyle"),
    seg(10974.8, "the utility customer service fund um and", speaker="Becky Doyle"),
    seg(10990.0, "Thank you. Questions?", speaker="unknown", turn=True),
]
DOYLE_QUOTE = (
    "uh to to recognize that change and we will be changing the name of the fund to "
    "the utility customer service fund um and"
)


def test_the_speaker_labels_name_is_allowed_on_that_speakers_own_line():
    claims, report = check(
        [
            item(
                "Becky Doyle said the utility billing fund will be renamed the utility customer "
                "service fund.",
                DOYLE_QUOTE,
                "3:02:49",
            )
        ],
        segments=DOYLE,
    )
    assert report["dropped_items"] == []
    assert [c.text[:11] for c in claims] == ["Becky Doyle"]


def test_the_speaker_labels_name_is_not_allowed_on_someone_elses_line():
    claims, report = check(
        [item("Becky Doyle asked for questions.", "Thank you. Questions?", "3:03:10")],
        segments=DOYLE,
    )
    assert claims == []
    assert report["dropped_items"][0]["reason"] == (
        "term not in the official agenda or roster: Doyle"
    )
