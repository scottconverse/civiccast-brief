import pytest

from civiccast_brief.script.checker import check_script
from civiccast_brief.script.recap import Claim, Recap
from civiccast_brief.script.writer import (
    DISCLAIMER,
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
{"speaker": "S1", "text": "CivicCast Brief, Longmont, October twentieth, twenty twenty-six.", "claim_ids": ["c01"]}
{"speaker": "S2", "text": "This episode is AI-generated from the public meeting video; check the official record.", "claim_ids": []},
{"speaker": "CHAPTER", "text": "Minutes", "claim_ids": ["c02"]}
{"speaker": "S1", "text": "The council approved the minutes unanimously.", "claim_ids": ["c02"]}
```
"""


def test_parse_script_reads_fenced_json_lines_and_trailing_commas():
    lines = parse_script(ANSWER)
    assert [line.speaker for line in lines] == ["S1", "S2", "CHAPTER", "S1"]
    assert lines[2].is_chapter
    assert lines[0].claim_ids == ("c01",)


def test_parse_script_rejects_prose_and_unknown_speakers():
    with pytest.raises(ScriptFormatError):
        parse_script('Here is the script:\n{"speaker": "S1", "text": "Hi.", "claim_ids": []}')
    with pytest.raises(ScriptFormatError):
        parse_script('{"speaker": "Maya", "text": "Hi.", "claim_ids": []}')
    with pytest.raises(ScriptFormatError):
        parse_script("")


def test_render_txt_gives_speaker_and_chapter_lines_without_claim_ids():
    lines = parse_script(ANSWER)
    assert render_txt(lines).splitlines() == [
        "[S1] CivicCast Brief, Longmont, October twentieth, twenty twenty-six.",
        f"[S2] {DISCLAIMER}",
        "[CHAPTER Minutes]",
        "[S1] The council approved the minutes unanimously.",
    ]


def test_prompt_carries_claims_disclaimer_and_word_rules_only():
    prompt = build_prompt(RECAP, feedback="Line 4 says 7.")
    assert '"id": "c01"' in prompt and '"id": "c02"' in prompt
    assert DISCLAIMER in prompt
    assert "WRITE FOR THE EAR" in prompt
    assert "Line 4 says 7." in prompt
    assert "previous draft failed" in prompt
    assert "Ordinance twenty twenty-six dash sixty-nine" in prompt


def test_write_script_uses_the_model_answer_and_the_checker_accepts_it():
    lines = write_script(RECAP, lambda prompt: ANSWER.replace("```json", "").replace("```", ""))
    assert len(lines) == 4
    report = check_script(RECAP, lines)
    # The fixture script has no YouTube outro, so the check must flag it, and only it.
    assert {i.code for i in report.errors} == {"outro"}


def test_script_json_round_trips(tmp_path):
    lines = [
        ScriptLine("S1", "Hi there.", ("c01",)),
        ScriptLine("CHAPTER", "Minutes", ("c02",)),
    ]
    txt, js = tmp_path / "script.txt", tmp_path / "script.json"
    save_script(lines, txt, js)
    assert txt.read_text(encoding="utf-8") == "[S1] Hi there.\n[CHAPTER Minutes]\n"
    assert load_script(js) == lines


def test_a_chapter_title_starts_with_a_capital_letter():
    answer = ANSWER.replace('"text": "Minutes"', '"text": "public comment"')
    assert parse_script(answer)[2].text == "Public comment"
