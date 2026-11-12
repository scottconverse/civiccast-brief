from civiccast_brief.script.checker import check_script, model_check
from civiccast_brief.script.recap import Claim, Recap
from civiccast_brief.script.writer import DISCLAIMER, ScriptLine

RECAP = Recap(
    city="Longmont",
    body="City Council",
    meeting_date="October 20, 2026",
    video_id="rsagM9xn28Y",
    claims=(
        Claim(
            "c01",
            "Meeting",
            "The Longmont City Council met on October 20, 2026.",
            "0:00:00",
            "Longmont City Council Regular Session - October 20, 2026",
        ),
        Claim(
            "c02",
            "Ordinance 2026-69",
            "Council approved Ordinance 2026-69 six to one.",
            "0:21:00",
            "Council approved Ordinance 2026-69 six to one",
        ),
        Claim(
            "c03",
            "Live stream",
            "The meeting video is on the city's YouTube channel.",
            "0:05:20",
            "viewed at the city's YouTube channel",
        ),
    ),
)


def good_script() -> list[ScriptLine]:
    return [
        ScriptLine(
            "S1", "CivicCast Brief, Longmont, October twentieth, twenty twenty-six.", ("c01",)
        ),
        ScriptLine("S2", DISCLAIMER),
        ScriptLine("CHAPTER", "Ordinance twenty twenty-six dash sixty-nine", ("c02",)),
        ScriptLine(
            "S1",
            "Council approved Ordinance twenty twenty-six dash sixty-nine six to one.",
            ("c02",),
        ),
        ScriptLine("S2", "Right."),
        ScriptLine("S1", "That's a big number."),
        ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c03",)),
    ]


def errors_by_code(lines):
    report = check_script(RECAP, lines)
    return {(i.line, i.code) for i in report.errors}


def test_good_script_passes_with_no_warnings():
    report = check_script(RECAP, good_script())
    assert report.passed
    assert report.issues == []
    assert report.speech_lines == 6
    assert report.chapter_lines == 1


def test_added_number_fails():
    lines = good_script()
    lines[3] = ScriptLine(
        "S1", "Council approved Ordinance twenty twenty-six dash sixty-nine seven to one.", ("c02",)
    )
    assert (4, "number") in errors_by_code(lines)


def test_uncited_factual_line_fails():
    lines = good_script()
    lines[4] = ScriptLine("S2", "The council spent two hundred thousand dollars on buses.")
    assert (5, "uncited") in errors_by_code(lines)


def test_invented_name_fails():
    lines = good_script()
    lines[3] = ScriptLine(
        "S1",
        "Council member Marcen approved Ordinance twenty twenty-six dash sixty-nine.",
        ("c02",),
    )
    report = check_script(RECAP, lines)
    assert any(i.code == "name" and "Marcen" in i.message for i in report.errors)


def test_missing_disclaimer_fails():
    lines = good_script()
    lines[1] = ScriptLine("S2", "This episode is AI-generated.")
    assert (2, "disclaimer") in errors_by_code(lines)


def test_digits_in_spoken_text_fail():
    lines = good_script()
    lines[3] = ScriptLine("S1", "Council approved Ordinance 2026-69 six to one.", ("c02",))
    assert (4, "digits") in errors_by_code(lines)


def test_a_chapter_title_is_shown_not_spoken_so_it_may_use_digits():
    lines = good_script()
    lines[2] = ScriptLine("CHAPTER", "Ordinance 2026-69", ("c02",))
    assert check_script(RECAP, lines).passed
    # Its numbers must still be in the cited claim.
    lines[2] = ScriptLine("CHAPTER", "Ordinance 2026-70", ("c02",))
    assert (3, "number") in errors_by_code(lines)


def test_unknown_claim_id_fails():
    lines = good_script()
    lines[3] = ScriptLine(
        "S1", "Council approved Ordinance twenty twenty-six dash sixty-nine six to one.", ("c99",)
    )
    assert (4, "unknown_claim") in errors_by_code(lines)


def test_outro_must_point_to_youtube():
    lines = good_script()
    lines[-1] = ScriptLine("S2", "Thanks for listening.", ())
    assert any(code == "outro" for _line, code in errors_by_code(lines))


def test_reaction_and_question_pass_uncited():
    assert check_script(RECAP, good_script()).passed
    lines = good_script()
    lines[4] = ScriptLine("S2", "Did they vote on that?")
    assert check_script(RECAP, lines).passed


def test_model_check_adds_warnings_for_unsupported_lines():
    answers = iter(
        [
            '{"states_fact": true, "supported": true, "problem": ""}',
            '{"states_fact": false, "supported": true, "problem": ""}',
            '{"states_fact": true, "supported": false, "problem": "eleven"}',
            '{"states_fact": false, "supported": true, "problem": ""}',
            '{"states_fact": false, "supported": true, "problem": ""}',
            '{"states_fact": false, "supported": true, "problem": ""}',
            '{"states_fact": false, "supported": true, "problem": ""}',
        ]
    )
    report = model_check(check_script(RECAP, good_script()), RECAP, lambda _prompt: next(answers))
    assert report.passed  # model disagreement is a warning, not a failure
    assert any(i.code == "model" and i.message == "eleven" for i in report.issues)
    assert "1 disagreement" in report.render()


def test_model_unparsed_answer_is_reported_not_crashed():
    report = model_check(check_script(RECAP, good_script()), RECAP, lambda _prompt: "I cannot say.")
    assert report.model_verdicts is not None
    assert all(v.supported is None for v in report.model_verdicts)
    assert any(i.code == "model" for i in report.issues)


def test_number_words_at_a_sentence_start_are_not_names():
    from civiccast_brief.script.checker import _proper_nouns

    assert _proper_nouns("Five to two on that vote.") == []
    assert _proper_nouns("Eighty-five thirteen Saint Brain Road annexation.") == [
        "Saint",
        "Brain",
        "Road",
    ]


def test_intro_and_outro_may_use_the_recap_header_without_a_claim():
    lines = good_script()
    lines[0] = ScriptLine(
        "S1",
        "Welcome to CivicCast Brief. We're covering the Longmont City Council meeting on "
        "October twentieth, twenty twenty-six.",
    )
    lines[-1] = ScriptLine("S2", "That's our brief. Watch the meeting video on YouTube.")
    report = check_script(RECAP, lines)
    assert report.passed, report.render()


def test_intro_may_not_add_facts_beyond_the_header():
    lines = good_script()
    lines[0] = ScriptLine(
        "S1", "Welcome to CivicCast Brief. Mayor Smith ran the Longmont meeting for three hours."
    )
    codes = errors_by_code(lines)
    assert (1, "name") in codes and (1, "number") in codes


def test_a_middle_line_still_needs_a_claim_for_header_facts():
    lines = good_script()
    lines[4] = ScriptLine("S2", "The Longmont City Council met on October twentieth.")
    assert (5, "uncited") in errors_by_code(lines)


def test_two_chapter_marks_together_fail_the_check():
    # The audio cannot place a chapter with no line in it, so a passing script must not hold one.
    lines = good_script()
    lines.insert(2, ScriptLine("CHAPTER", "October 20 meeting", ("c01",)))
    assert lines[3].is_chapter
    report = check_script(RECAP, lines)
    assert not report.passed
    assert [i.code for i in report.errors] == ["empty_chapter"]
