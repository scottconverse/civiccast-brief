import json

import pytest

from civiccast_brief.script.checker import check_script
from civiccast_brief.script.recap import Claim, Recap
from civiccast_brief.script.writer import (
    ScriptFormatError,
    ScriptLine,
    build_prompt,
    load_script,
    parse_script,
    render_txt,
    save_script,
    write_script,
)

RECAP = Recap(
    city="Longmont",
    body="City Council",
    meeting_date="October 20, 2026",
    video_id="rsagM9xn28Y",
    claims=(
        Claim(
            "c01", "Meeting", "The Longmont City Council met on October 20, 2026.", "0:00:00", "x"
        ),
        Claim("c02", "Minutes", "The council approved the minutes unanimously.", "0:07:34", "y"),
    ),
)

ANSWER = """```json
{"speaker": "S1", "text": "CivicCast Brief, Longmont, October twentieth, twenty twenty-six.", "claim_ids": ["c01"]},
{"speaker": "CHAPTER", "text": "Minutes", "claim_ids": ["c02"]}
{"speaker": "S1", "text": "The council approved the minutes unanimously.", "claim_ids": ["c02"]}
```
"""


def test_parse_script_reads_fenced_json_lines_and_trailing_commas():
    lines = parse_script(ANSWER)
    assert [line.speaker for line in lines] == ["S1", "CHAPTER", "S1"]
    assert lines[1].is_chapter
    assert lines[0].claim_ids == ("c01",)


def test_parse_script_rejects_prose_and_unknown_speakers():
    with pytest.raises(ScriptFormatError):
        parse_script('Here is the script:\n{"speaker": "S1", "text": "Hi.", "claim_ids": []}')
    with pytest.raises(ScriptFormatError):
        parse_script('{"speaker": "Pat", "text": "Hi.", "claim_ids": []}')
    with pytest.raises(ScriptFormatError):
        parse_script("")


def test_render_txt_gives_speaker_and_chapter_lines_without_claim_ids():
    lines = parse_script(ANSWER)
    assert render_txt(lines).splitlines() == [
        "[S1] CivicCast Brief, Longmont, October twentieth, twenty twenty-six.",
        "[CHAPTER Minutes]",
        "[S1] The council approved the minutes unanimously.",
    ]


def test_prompt_carries_claims_and_word_rules_and_no_disclaimer():
    prompt = build_prompt(RECAP, feedback="Line 4 says 7.")
    assert '"id": "c01"' in prompt and '"id": "c02"' in prompt
    assert "AI-generated" not in prompt
    assert "disclaimer" not in prompt.lower()
    assert "WRITE FOR THE EAR" in prompt
    assert "Line 4 says 7." in prompt
    assert "previous draft failed" in prompt
    assert "Ordinance twenty twenty-six dash sixty-nine" in prompt


def test_write_script_uses_the_model_answer_and_the_checker_accepts_it():
    lines = write_script(RECAP, lambda prompt: ANSWER.replace("```json", "").replace("```", ""))
    assert len(lines) == 3
    report = check_script(RECAP, lines)
    # The fixture script has no YouTube outro, so the check must flag it, and only it.
    assert {i.code for i in report.errors} == {"outro"}


def test_old_disclaimer_is_dropped_on_load_and_render(tmp_path):
    js = tmp_path / "script.json"
    js.write_text(
        json.dumps(
            [
                {"speaker": "S1", "text": "CivicCast Brief, Longmont.", "claim_ids": []},
                {
                    "speaker": "S2",
                    "text": "This episode is AI-generated from the public meeting video; check the official record.",
                    "claim_ids": [],
                },
                {"speaker": "S2", "text": "Watch on YouTube.", "claim_ids": []},
            ]
        ),
        encoding="utf-8",
    )
    lines = load_script(js)
    assert len(lines) == 2
    assert "AI-generated" not in render_txt(lines)


def test_script_json_round_trips(tmp_path):
    lines = [
        ScriptLine("S1", "Hi there.", ("c01",)),
        ScriptLine("CHAPTER", "Minutes", ("c02",)),
    ]
    txt, js = tmp_path / "script.txt", tmp_path / "script.json"
    save_script(lines, txt, js)
    assert txt.read_text(encoding="utf-8") == "[S1] Hi there.\n[CHAPTER Minutes]\n"
    assert load_script(js) == lines


def test_a_roster_members_bare_first_name_prints_as_the_full_record_name():
    from civiccast_brief.meeting.roster import roster_from_dict

    roster = roster_from_dict(
        {
            "city": "Boulder",
            "body": "City Council",
            "state": "CO",
            "members": [
                {
                    "name": "Rob Kaplan",
                    "first": "Rob",
                    "last": "Kaplan",
                    "role": "City Council Member",
                },
                {"name": "Tara Winer", "first": "Tara", "last": "Winer", "role": "Mayor Pro Tem"},
            ],
        }
    )
    recap = Recap(
        city="Boulder",
        body="City Council",
        meeting_date="November 12, 2026",
        video_id="n8OxJlE_2ME",
        claims=(
            Claim(
                "c01",
                "Audit",
                "An unidentified speaker said the item came from Rob.",
                "0:10:00",
                "the item came from Rob",
            ),
        ),
    )
    answer = (
        '{"speaker": "S1", "text": "It came from Rob.", "claim_ids": ["c01"]}\n'
        '{"speaker": "S2", "text": "Rob\'s proposal moved on.", "claim_ids": ["c01"]}\n'
        '{"speaker": "S1", "text": "Rob Kaplan spoke.", "claim_ids": ["c01"]}'
    )
    lines = write_script(recap, lambda prompt: answer, roster=roster)
    assert [line.text for line in lines] == [
        "It came from Rob Kaplan.",
        "Rob Kaplan's proposal moved on.",
        "Rob Kaplan spoke.",
    ]


def _roster_with(first: str, name: str, last: str):
    from civiccast_brief.meeting.roster import roster_from_dict

    return roster_from_dict(
        {
            "city": "Boulder",
            "body": "City Council",
            "state": "CO",
            "members": [
                {"name": name, "first": first, "last": last, "role": "City Council Member"}
            ],
        }
    )


def test_a_first_name_before_another_capital_word_is_a_different_person():
    """ "Rob Smith" is not the member Rob Kaplan: a first name followed by any capitalised word
    stays as written. Only a possessive or a speech/vote verb lets a sentence-opening first
    name expand, and a lower-case word never changes."""
    from civiccast_brief.script.writer import expand_first_names

    roster = _roster_with("Rob", "Rob Kaplan", "Kaplan")
    lines = [
        ScriptLine("S1", "Rob Smith spoke."),
        ScriptLine("S1", "I heard Rob Smith spoke."),
        ScriptLine("S1", "It came from Rob."),
        ScriptLine("S1", "In favor of Rob's proposal."),
        ScriptLine("S1", "rob said it."),
    ]
    texts = [line.text for line in expand_first_names(lines, roster)]
    assert texts == [
        "Rob Smith spoke.",
        "I heard Rob Smith spoke.",
        "It came from Rob Kaplan.",
        "In favor of Rob Kaplan's proposal.",
        "rob said it.",
    ]


def test_a_sentence_opening_word_that_is_also_a_first_name_stays_unless_it_speaks_or_votes():
    """ "Will the board act?" is not the member Will Stone. At the start of a sentence the word
    expands only with a possessive or directly before a speech or vote verb."""
    from civiccast_brief.script.writer import expand_first_names

    roster = _roster_with("Will", "Will Stone", "Stone")
    lines = [
        ScriptLine("S1", "Will the board act?"),
        ScriptLine("S1", "Will Stone moved approval."),
        ScriptLine("S1", "Will said the board would act."),
        ScriptLine("S1", "Will voted no."),
        ScriptLine("S1", "Will's question stands."),
    ]
    texts = [line.text for line in expand_first_names(lines, roster)]
    assert texts == [
        "Will the board act?",
        "Will Stone moved approval.",
        "Will Stone said the board would act.",
        "Will Stone voted no.",
        "Will Stone's question stands.",
    ]


def test_the_checker_accepts_the_rosters_full_name_for_the_cited_first_name():
    """The writer expands "Rob" to "Rob Kaplan"; the surname is not in the claim. The roster
    says which one member the first name means, so the checker allows that full name, and only
    when the cited claim prints the first name."""
    from civiccast_brief.meeting.roster import roster_from_dict

    roster = roster_from_dict(
        {
            "city": "Boulder",
            "body": "City Council",
            "state": "CO",
            "members": [
                {"name": "Rob Kaplan", "first": "Rob", "last": "Kaplan", "role": "Council Member"}
            ],
        }
    )
    recap = Recap(
        city="Boulder",
        body="City Council",
        meeting_date="November 5, 2026",
        video_id="n8OxJlE_2ME",
        claims=(
            Claim(
                "c01", "Item", "Rob raised a question about the budget.", "0:10:00", "Rob raised"
            ),
        ),
    )
    lines = [
        ScriptLine("S1", "CivicCast Brief, Boulder."),
        ScriptLine("S1", "Rob Kaplan raised a question about the budget.", ("c01",)),
        ScriptLine("S2", "Watch the meeting video on YouTube."),
    ]
    without = check_script(recap, lines)
    assert any(i.code == "name" and "Kaplan" in i.message for i in without.errors)
    assert check_script(recap, lines, roster=roster).passed
