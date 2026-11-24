from civiccast_brief.script.checker import _named_voters, _partial_vote, check_script, model_check
from civiccast_brief.script.recap import Claim, Recap
from civiccast_brief.script.writer import ScriptLine

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
        ScriptLine("CHAPTER", "Ordinance twenty twenty-six dash sixty-nine", ("c02",)),
        ScriptLine(
            "S2",
            "Council approved Ordinance twenty twenty-six dash sixty-nine six to one.",
            ("c02",),
        ),
        ScriptLine("S1", "Right."),
        ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c03",)),
    ]


def errors_by_code(lines):
    report = check_script(RECAP, lines)
    return {(i.line, i.code) for i in report.errors}


def test_good_script_passes_with_no_warnings():
    report = check_script(RECAP, good_script())
    assert report.passed
    assert report.issues == []
    assert report.speech_lines == 4
    assert report.chapter_lines == 1


def test_added_number_fails():
    lines = good_script()
    lines[2] = ScriptLine(
        "S2", "Council approved Ordinance twenty twenty-six dash sixty-nine seven to one.", ("c02",)
    )
    assert (3, "number") in errors_by_code(lines)


def test_uncited_factual_line_fails():
    lines = good_script()
    lines[3] = ScriptLine("S1", "The council spent two hundred thousand dollars on buses.")
    assert (4, "uncited") in errors_by_code(lines)


def test_invented_name_fails():
    lines = good_script()
    lines[2] = ScriptLine(
        "S2",
        "Council member Marcen approved Ordinance twenty twenty-six dash sixty-nine.",
        ("c02",),
    )
    report = check_script(RECAP, lines)
    assert any(i.code == "name" and "Marcen" in i.message for i in report.errors)


def test_old_disclaimer_line_is_dropped_before_checking():
    lines = good_script()
    lines.insert(
        1,
        ScriptLine(
            "S2",
            "This episode is AI-generated from the public meeting video; check the official record.",
        ),
    )
    report = check_script(RECAP, lines)
    assert report.passed, report.render()
    assert all("AI-generated" not in line.text for line in report.lines)


def test_old_host_names_still_pass_the_name_check():
    lines = good_script()
    lines[0] = ScriptLine("S1", "CivicCast Brief. I'm Maya, and this is Theo.")
    report = check_script(RECAP, lines)
    assert not [i for i in report.errors if i.code == "name"], report.render()


def test_digits_in_spoken_text_fail():
    lines = good_script()
    lines[2] = ScriptLine("S2", "Council approved Ordinance 2026-69 six to one.", ("c02",))
    assert (3, "digits") in errors_by_code(lines)


def test_a_chapter_title_is_shown_not_spoken_so_it_may_use_digits():
    lines = good_script()
    lines[1] = ScriptLine("CHAPTER", "Ordinance 2026-69", ("c02",))
    assert check_script(RECAP, lines).passed
    # Its numbers must still be in the cited claim.
    lines[1] = ScriptLine("CHAPTER", "Ordinance 2026-70", ("c02",))
    assert (2, "number") in errors_by_code(lines)


def test_unknown_claim_id_fails():
    lines = good_script()
    lines[2] = ScriptLine(
        "S2", "Council approved Ordinance twenty twenty-six dash sixty-nine six to one.", ("c99",)
    )
    assert (3, "unknown_claim") in errors_by_code(lines)


def test_outro_must_point_to_youtube():
    lines = good_script()
    lines[-1] = ScriptLine("S2", "Thanks for listening.", ())
    assert any(code == "outro" for _line, code in errors_by_code(lines))


def test_reaction_and_question_pass_uncited():
    assert check_script(RECAP, good_script()).passed
    lines = good_script()
    lines[3] = ScriptLine("S1", "Did they vote on that?")
    assert check_script(RECAP, lines).passed


def test_model_check_adds_warnings_for_unsupported_lines():
    answers = iter(
        [
            '{"states_fact": true, "supported": true, "problem": ""}',
            '{"states_fact": true, "supported": false, "problem": "eleven"}',
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


def test_tonight_at_a_sentence_start_is_not_a_name():
    lines = good_script()
    lines[0] = ScriptLine(
        "S1",
        "Welcome to CivicCast Brief. I'm Pat. Tonight we're covering the Longmont City Council "
        "meeting on October twentieth, twenty twenty-six.",
    )
    report = check_script(RECAP, lines)
    assert report.passed, report.render()


def test_an_unknown_name_at_a_sentence_start_still_fails():
    lines = good_script()
    lines[0] = ScriptLine(
        "S1",
        "Welcome to CivicCast Brief. I'm Pat. Zorbax we're covering the Longmont City Council "
        "meeting on October twentieth, twenty twenty-six.",
    )
    codes = errors_by_code(lines)
    assert (1, "name") in codes


def test_the_closing_line_openers_are_not_names():
    lines = good_script()
    lines[-1] = ScriptLine(
        "S2",
        "You can watch the meeting video on the city's YouTube channel. That's the CivicCast "
        "Brief, folks. We'll see you next time. Take care, everyone.",
        ("c03",),
    )
    report = check_script(RECAP, lines)
    assert report.passed, report.render()


def test_an_unknown_name_at_a_sentence_start_in_the_closing_line_still_fails():
    lines = good_script()
    lines[-1] = ScriptLine(
        "S2",
        "You can watch the meeting video on the city's YouTube channel. Zorbax see you next time.",
        ("c03",),
    )
    codes = errors_by_code(lines)
    assert (5, "name") in codes


def test_intro_may_not_add_facts_beyond_the_header():
    lines = good_script()
    lines[0] = ScriptLine(
        "S1", "Welcome to CivicCast Brief. Mayor Smith ran the Longmont meeting for three hours."
    )
    codes = errors_by_code(lines)
    assert (1, "name") in codes and (1, "number") in codes


def test_a_middle_line_still_needs_a_claim_for_header_facts():
    lines = good_script()
    lines[3] = ScriptLine("S1", "The Longmont City Council met on October twentieth.")
    assert (4, "uncited") in errors_by_code(lines)


def test_two_chapter_marks_together_fail_the_check():
    # The audio cannot place a chapter with no line in it, so a passing script must not hold one.
    lines = good_script()
    lines.insert(1, ScriptLine("CHAPTER", "October 20 meeting", ("c01",)))
    assert lines[2].is_chapter
    report = check_script(RECAP, lines)
    assert not report.passed
    assert [i.code for i in report.errors] == ["empty_chapter"]


# Boulder, Nov 19 2026: a supported script failed on "Ideas" and "Others" at a sentence start,
# on "eight fifty-nine p.m." for a claim's "8:59 p.m.", and on "Boulder's" in the outro.
CLOSE = Recap(
    city="Boulder",
    body="City Council",
    meeting_date="November 12, 2026",
    video_id="n8OxJlE_2xx",
    claims=(
        Claim("c1", "Ideas", "A speaker suggested getting legal advice.", "1:00:00", "legal"),
        Claim("c2", "Close", "A speaker closed the meeting at 8:59 p.m.", "2:00:00", "close"),
    ),
)


def _close_report(middle: list[ScriptLine], outro: str):
    return check_script(
        CLOSE,
        [
            ScriptLine("S1", "Welcome to CivicCast Brief, with the Boulder City Council."),
            *middle,
            ScriptLine("S2", outro),
        ],
    )


def test_plural_openers_clock_times_and_a_possessive_place_pass():
    report = _close_report(
        [
            ScriptLine("S1", "Ideas included legal advice.", ("c1",)),
            ScriptLine("S2", "Others agreed. The meeting closed at eight fifty-nine p.m.", ("c2",)),
        ],
        "That's the brief. Watch the full meeting video on the City of Boulder's YouTube channel.",
    )
    assert report.passed, report.render()


def test_a_wrong_clock_time_still_fails():
    report = _close_report(
        [ScriptLine("S2", "The meeting closed at nine fifty-nine p.m.", ("c2",))],
        "Watch the meeting video on YouTube.",
    )
    assert any(i.code == "number" for i in report.errors)


def test_a_possessive_invented_name_still_fails():
    report = _close_report(
        [ScriptLine("S1", "Smith's idea was legal advice.", ("c1",))],
        "Watch the meeting video on YouTube.",
    )
    assert any(i.code == "name" and "Smith" in i.message for i in report.errors)


def test_a_one_the_claim_says_may_be_spoken():
    phase = Recap(
        city="Boulder",
        body="City Council",
        meeting_date="November 12, 2026",
        video_id="n8OxJlE_2xx",
        claims=(
            Claim(
                "c1",
                "Arts",
                "A speaker said it is built in phase one of the project.",
                "1:00:00",
                "arts",
            ),
        ),
    )
    lines = [
        ScriptLine("S1", "Welcome to CivicCast Brief, with the Boulder City Council."),
        ScriptLine("S1", "It is built in phase one, a speaker said.", ("c1",)),
        ScriptLine("S2", "Watch the meeting video on YouTube."),
    ]
    assert check_script(phase, lines).passed
    lines[1] = ScriptLine("S1", "It is built in phase two, a speaker said.", ("c1",))
    assert any(i.code == "number" for i in check_script(phase, lines).errors)


VOTE_RECAP = Recap(
    city="Boulder",
    body="City Council",
    meeting_date="November 12, 2026",
    video_id="n8OxJlE_2ME",
    claims=(
        Claim("c01", "Meeting", "The Boulder City Council met November 12, 2026.", "0:00:00", "x"),
        Claim("c02", "Use review", "Schuchard voted yes.", "0:20:00", "Schuchard voted yes"),
        Claim("c03", "Use review", "Speer voted yes.", "0:20:00", "Speer voted yes"),
        Claim("c04", "Use review", "Benjamin voted yes.", "0:20:00", "Benjamin voted yes"),
        Claim("c05", "Use review", "Marquis voted yes.", "0:20:00", "Marquis voted yes"),
        Claim("c06", "Use review", "Brockett voted yes.", "0:20:00", "Brockett voted yes"),
        Claim("c07", "Use review", "Adams voted no.", "0:20:00", "Adams voted no"),
        Claim("c08", "Use review", "Kaplan voted no.", "0:20:00", "Kaplan voted no"),
        Claim("c09", "Use review", "Winer voted no.", "0:20:00", "Winer voted no"),
        Claim("c10", "Use review", "The motion passed five to three.", "0:20:00", "five to three"),
        Claim("c11", "Video", "The video is on the city's YouTube channel.", "0:00:00", "YouTube"),
    ),
)
VOTE_IDS = ("c02", "c03", "c04", "c05", "c06", "c07", "c08", "c09", "c10")


def _vote_errors(lines):
    report = check_script(VOTE_RECAP, lines)
    return {(i.line, i.code) for i in report.errors}


def _vote_script(text: str) -> list[ScriptLine]:
    return [
        ScriptLine(
            "S1", "CivicCast Brief, Boulder, November twelfth, twenty twenty-six.", ("c01",)
        ),
        ScriptLine("S2", text, VOTE_IDS),
        ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c11",)),
    ]


def test_a_partial_list_of_voters_beside_the_count_fails():
    lines = _vote_script(
        "The motion passed five to three. Schuchard, Speer, Benjamin and Brockett voted yes. "
        "Adams and Kaplan voted no."
    )
    assert (2, "partial_vote") in _vote_errors(lines)


def test_the_count_alone_passes():
    lines = _vote_script("The motion passed five to three.")
    assert all(code != "partial_vote" for _line, code in _vote_errors(lines))


def test_naming_every_voter_on_each_side_passes():
    lines = _vote_script(
        "The motion passed five to three. Marquis, Schuchard, Speer, Benjamin and Brockett "
        "voted yes. Winer, Adams and Kaplan voted no."
    )
    assert all(code != "partial_vote" for _line, code in _vote_errors(lines))


def test_a_sentence_with_no_vote_word_is_not_a_partial_vote():
    """ "Adams and Kaplan raised no objection" says there was no opposition; it does not name
    two of the three no voters."""
    lines = _vote_script("Adams and Kaplan raised no objection.")
    assert all(code != "partial_vote" for _line, code in _vote_errors(lines))


def test_a_rate_range_is_not_read_as_a_vote_tally():
    """ "from 3 to 5 percent" is a number range, not a tally: a claim's numbers become a vote
    count only when the claim also says vote, voted, roll call or motion."""
    recap = Recap(
        city="Boulder",
        body="City Council",
        meeting_date="November 12, 2026",
        video_id="n8OxJlE_2ME",
        claims=(
            Claim("c01", "Fees", "The fee rose from 3 to 5 percent.", "0:10:00", "x"),
            Claim("c02", "Vote", "Adams voted yes.", "0:20:00", "Adams voted yes"),
            Claim("c03", "Vote", "Kaplan voted yes.", "0:20:00", "Kaplan voted yes"),
            Claim("c04", "Video", "The video is on the city's YouTube channel.", "0:00:00", "x"),
        ),
    )
    lines = [
        ScriptLine("S1", "CivicCast Brief, Boulder."),
        ScriptLine("S2", "Adams and Kaplan voted yes.", ("c01", "c02", "c03")),
        ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c04",)),
    ]
    assert all(i.code != "partial_vote" for i in check_script(recap, lines).errors)


def test_two_different_tallies_leave_the_voters_untied_and_skip_the_check():
    recap = Recap(
        city="Boulder",
        body="City Council",
        meeting_date="November 12, 2026",
        video_id="n8OxJlE_2ME",
        claims=(
            Claim("c01", "Vote", "The motion passed five to three.", "0:20:00", "x"),
            Claim("c02", "Vote", "The other vote passed four to two.", "0:30:00", "x"),
            Claim("c03", "Vote", "Adams voted yes.", "0:20:00", "Adams voted yes"),
            Claim("c04", "Vote", "Kaplan voted yes.", "0:20:00", "Kaplan voted yes"),
            Claim("c05", "Video", "The video is on the city's YouTube channel.", "0:00:00", "x"),
        ),
    )
    lines = [
        ScriptLine("S1", "CivicCast Brief, Boulder."),
        ScriptLine("S2", "Adams voted yes.", ("c01", "c02", "c03")),
        ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c05",)),
    ]
    assert all(i.code != "partial_vote" for i in check_script(recap, lines).errors)


def test_a_lowercase_body_is_not_a_voter():
    """Confirmed bug: IGNORECASE let "council" match a [A-Z] name, so a body counted as a
    voter and a named sentence looked partial."""
    voters = _named_voters(["The council voted yes on the plan.", "Adams voted yes."])
    assert voters == {"yes": {"Adams"}, "no": set()}


def test_a_capitalised_body_is_not_a_voter():
    voters = _named_voters(
        [
            "The Council voted yes.",
            "The Board voted no.",
            "Council voted yes",
            "Commission voted no",
        ]
    )
    assert voters == {"yes": set(), "no": set()}


def test_a_council_member_surname_still_counts_as_a_voter():
    voters = _named_voters(["Council Member Adams voted no."])
    assert voters == {"yes": set(), "no": {"Adams"}}


def test_a_body_beside_a_named_voter_is_not_a_partial_vote():
    assert _partial_vote("Adams voted yes.", ["The council voted yes.", "Adams voted yes."]) == []


CONSENT_RECAP = Recap(
    city="Boulder",
    body="City Council",
    meeting_date="November 12, 2026",
    video_id="n8OxJlE_2ME",
    claims=(
        Claim(
            "c01",
            "Consent agenda",
            "The consent agenda was approved unanimously, with yes votes from Aaron Brockett, "
            "Nicole Speer, Ryan Schuchard and Tara Winer.",
            "0:08:10",
            "yes votes from Aaron Brockett, Nicole Speer, Ryan Schuchard and Tara Winer",
        ),
        Claim("c02", "Video", "The video is on the city's YouTube channel.", "0:00:00", "YouTube"),
    ),
)


def _consent_script(text: str) -> list[ScriptLine]:
    return [
        ScriptLine(
            "S1", "CivicCast Brief, Boulder, November twelfth, twenty twenty-six.", ("c01",)
        ),
        ScriptLine("S2", text, ("c01",)),
        ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c02",)),
    ]


def test_a_plural_civic_title_is_not_a_name_in_a_true_vote_line():
    """The title "Members" is not a fact the claim must print; the voters' names still are."""
    report = check_script(
        CONSENT_RECAP,
        _consent_script(
            "The consent agenda was also approved unanimously, with yes votes from Aaron "
            "Brockett and Council Members Nicole Speer, Ryan Schuchard and Tara Winer."
        ),
    )
    assert report.passed, report.render()


KAPLAN_RECAP = Recap(
    city="Boulder",
    body="City Council",
    meeting_date="November 12, 2026",
    video_id="n8OxJlE_2ME",
    claims=(
        Claim("c01", "Vote", "Rob Kaplan voted no.", "0:20:00", "Rob Kaplan voted no"),
        Claim("c02", "Video", "The video is on the city's YouTube channel.", "0:00:00", "YouTube"),
    ),
)


def test_a_singular_civic_title_gives_a_claim_an_office_to_meet():
    """A singular title gives a specific office, so the cited claim must print it.

    "Mayor Rob Kaplan voted no." is wrong when the claim says only "Rob Kaplan voted no."
    """
    report = check_script(
        KAPLAN_RECAP,
        [
            ScriptLine("S1", "CivicCast Brief, Boulder, November twelfth, twenty twenty-six."),
            ScriptLine("S2", "Mayor Rob Kaplan voted no.", ("c01",)),
            ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c02",)),
        ],
    )
    assert any(i.code == "name" and "'Mayor'" in i.message for i in report.errors), report.render()


def test_an_invented_name_after_a_civic_title_still_fails():
    report = check_script(
        CONSENT_RECAP,
        _consent_script(
            "The consent agenda was approved with yes votes from Council Members Bob Quorbex."
        ),
    )
    assert any(i.code == "name" and "Quorbex" in i.message for i in report.errors)


def test_a_word_before_a_plural_title_is_still_a_name():
    """Only "Council" and "Board" join a plural title; any other word before one is checked.

    "Quorbex Trustees voted no." names an invented body, and nothing in the cited claim
    prints "Quorbex", so the check must fail on it.
    """
    recap = Recap(
        city="Boulder",
        body="City Council",
        meeting_date="November 12, 2026",
        video_id="n8OxJlE_2ME",
        claims=(
            Claim("c01", "Vote", "The Trustees voted no on the ordinance.", "0:20:00", "voted no"),
            Claim(
                "c02", "Video", "The video is on the city's YouTube channel.", "0:00:00", "YouTube"
            ),
        ),
    )
    report = check_script(
        recap,
        [
            ScriptLine("S1", "CivicCast Brief, Boulder, November twelfth, twenty twenty-six."),
            ScriptLine("S2", "Quorbex Trustees voted no.", ("c01",)),
            ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c02",)),
        ],
    )
    assert any(i.code == "name" and "Quorbex" in i.message for i in report.errors), report.render()
