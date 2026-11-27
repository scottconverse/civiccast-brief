"""The article check: every paragraph cites claims, and adds no number or name they lack."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from civiccast_brief.article.terms import load_record
from civiccast_brief.article.writer import (
    Article,
    ArticleFormatError,
    Paragraph,
    Quote,
    Section,
    Vote,
    check_article,
    citations,
    drop_unsupported,
    missing_money,
    parse_article,
    render_markdown,
    site_article,
)
from civiccast_brief.script.recap import Claim, Recap

RECAP = Recap(
    city="Longmont",
    body="City Council",
    meeting_date="October 20, 2026",
    video_id="uHFuCfd5MVQ",
    claims=(
        Claim(
            id="c01",
            topic="Annexation",
            text="The council approved the 8513 St. Vrain Road annexation 6-1.",
            timestamp="1:02:03",
            quote="the motion carries six to one",
        ),
        Claim(
            id="c02",
            topic="Public comment",
            text="A speaker asked the council to keep the Sunset pool open.",
            timestamp="0:10:00",
            quote="please keep Sunset pool open",
        ),
    ),
)


def article(*paragraphs: Paragraph, headline: str = "Council approves annexation") -> Article:
    return Article(headline=headline, sections=(Section(None, paragraphs),))


def test_a_paragraph_that_restates_its_claims_passes() -> None:
    report = check_article(
        RECAP,
        article(
            Paragraph("The Longmont City Council approved the annexation 6-1.", ("c01",)),
            Paragraph("One speaker asked the council to keep the Sunset pool open.", ("c02",)),
        ),
    )
    assert report.passed, report.render()


def test_a_number_the_claims_lack_fails() -> None:
    report = check_article(
        RECAP, article(Paragraph("The council approved the annexation 7-0.", ("c01",)))
    )
    assert [i.code for i in report.issues] == ["number", "number"]


def test_a_name_the_claims_lack_fails() -> None:
    report = check_article(
        RECAP, article(Paragraph("Mayor Waters said the council approved it.", ("c01",)))
    )
    assert {i.message for i in report.issues} == {
        "name 'Mayor' is not in the cited claims",
        "name 'Waters' is not in the cited claims",
    }


def test_a_paragraph_without_a_claim_or_with_an_unknown_one_fails() -> None:
    report = check_article(
        RECAP,
        article(Paragraph("The council met.", ()), Paragraph("It voted.", ("c09",))),
    )
    assert {(i.where, i.code) for i in report.issues} == {
        ("paragraph 1", "uncited"),
        ("paragraph 2", "unknown_claim"),
        ("paragraph 2", "uncited"),
        ("headline", "unknown_claim"),
        ("headline", "uncited"),
        # The story cites nothing the check can call the meeting's news.
        ("lead", "news_lead"),
    }


def test_the_headline_is_checked_against_the_claims_the_article_cites() -> None:
    report = check_article(
        RECAP,
        article(
            Paragraph("The council approved the annexation 6-1.", ("c01",)),
            headline="Council closes Sunset pool",
        ),
    )
    assert [(i.where, i.code) for i in report.issues] == [
        ("headline", "name"),
        # The headline names no money, land or law vote, the meeting's biggest news.
        ("headline", "news_headline"),
    ]


def test_one_speaker_is_not_a_count_but_one_ordinance_is() -> None:
    lead = Paragraph("The council approved the annexation 6-1.", ("c01",))
    passes = Paragraph("One speaker asked to keep the Sunset pool open.", ("c02",))
    fails = Paragraph("One ordinance asked to keep the Sunset pool open.", ("c02",))
    assert check_article(RECAP, article(lead, passes)).passed
    assert [i.code for i in check_article(RECAP, article(lead, fails)).issues] == ["number"]


def test_a_common_word_that_opens_a_sentence_is_not_a_name_but_a_name_still_is() -> None:
    lead = Paragraph("The council approved the annexation 6-1.", ("c01",))
    text = "During public comment, one speaker asked to keep the Sunset pool open."
    assert check_article(RECAP, article(lead, Paragraph(text, ("c02",)))).passed
    named = "During public comment, Waters asked to keep the Sunset pool open."
    issues = check_article(RECAP, article(lead, Paragraph(named, ("c02",)))).issues
    assert [i.message for i in issues] == ["name 'Waters' is not in the cited claims"]


def test_a_possessive_of_an_allowed_name_passes_but_of_an_unknown_name_fails() -> None:
    lead = Paragraph("The council approved the annexation 6-1.", ("c01",))
    city = check_article(RECAP, article(lead, headline="Longmont's council approves annexation"))
    assert "name" not in {i.code for i in city.issues}, city.render()
    other = check_article(RECAP, article(lead, headline="Waters's council approves annexation"))
    assert ("headline", "name") in {(i.where, i.code) for i in other.issues}


def test_the_prompt_does_not_show_the_unverified_topics() -> None:
    from civiccast_brief.article.writer import build_prompt

    prompt = build_prompt(RECAP)
    assert "Public comment" not in prompt
    assert "Sunset pool" in prompt


def test_drop_unsupported_removes_only_the_failing_paragraph() -> None:
    good = Paragraph("The council approved the annexation 6-1.", ("c01",))
    bad = Paragraph("The council approved the annexation 7-0.", ("c01",))
    kept, dropped = drop_unsupported(RECAP, article(good, bad))
    assert kept.paragraphs == (good,)
    assert dropped.paragraphs == [bad]
    assert check_article(RECAP, kept).passed


def test_each_paragraph_links_to_the_video_moment_of_its_claims() -> None:
    paragraph = Paragraph("The council approved the annexation 6-1.", ("c01",))
    [cite] = citations(RECAP, paragraph)
    assert cite["url"] == "https://www.youtube.com/watch?v=uHFuCfd5MVQ&t=3723s"
    assert cite["quote"] == "the motion carries six to one"
    assert "[1:02:03](https://www.youtube.com/watch?v=uHFuCfd5MVQ&t=3723s)" in render_markdown(
        RECAP, article(paragraph)
    )


def test_a_claim_with_no_video_moment_gets_no_citation() -> None:
    """A fact taken from the packet carries 0:00:00: there is no video moment to link."""
    recap = replace(
        RECAP,
        claims=RECAP.claims
        + (
            Claim(
                id="c03",
                topic="Packet",
                text="The packet lists the fee change.",
                timestamp="0:00:00",
                quote="the packet lists the fee change",
            ),
        ),
    )
    paragraph = Paragraph("The packet lists the fee change.", ("c03",))
    assert citations(recap, paragraph) == []
    markdown = render_markdown(recap, article(paragraph))
    assert "0:00:00" not in markdown
    assert "t=0s" not in markdown
    # No video moment and no document page: the record is the meeting, not the video.
    assert "(The meeting record)" in markdown
    assert "The meeting video" not in markdown


def test_a_genuine_moment_near_the_start_keeps_its_link() -> None:
    recap = replace(
        RECAP,
        claims=(
            Claim(
                id="c01",
                topic="Opening",
                text="The meeting opened at 8 p.m.",
                timestamp="0:00:05",
                quote="the meeting opened",
            ),
        ),
    )
    paragraph = Paragraph("The meeting opened at 8 p.m.", ("c01",))
    [cite] = citations(recap, paragraph)
    assert cite["url"].endswith("&t=5s")


def test_parse_reads_the_json_object_inside_the_answer() -> None:
    answer = "Here it is:\n" + json.dumps(
        {
            "headline": "H",
            "dek": "D",
            "sections": [{"heading": None, "paragraphs": [{"text": "T", "claim_ids": ["c01"]}]}],
            "votes": [{"motion": "M", "result": "passed", "tally": "6-1", "claim_id": "c01"}],
            "quotes": [{"claim_id": "c02", "speaker": "A speaker"}],
        }
    )
    assert parse_article(answer) == Article(
        "H",
        (Section(None, (Paragraph("T", ("c01",)),)),),
        dek="D",
        votes=(Vote("M", "passed", "6-1", "c01"),),
        quotes=(Quote("c02", "A speaker"),),
    )
    with pytest.raises(ArticleFormatError):
        parse_article('{"headline": "H", "sections": []}')


def _segments():
    from civiccast_brief.meeting.transcript import Segment

    return [
        Segment(3710.0, 3712.0, "Item 12, Ordinance O-2026-70, the", True),
        Segment(3720.0, 3722.0, ">> Council member Waters moves approval", True),
        Segment(3900.0, 3902.0, "Ordinance 2026-99 is next", True),
    ]


def test_a_name_or_number_said_near_the_claim_passes_and_one_said_later_fails() -> None:
    from civiccast_brief.article.writer import caption_windows

    windows = caption_windows(RECAP, _segments())
    assert "Waters" in windows["c01"] and "2026-99" not in windows["c01"]
    near = Paragraph("Waters moved the annexation Ordinance O-2026-70; it passed 6-1.", ("c01",))
    assert check_article(RECAP, article(near), windows).passed
    far = Paragraph("The council approved Ordinance 2026-99 6-1.", ("c01",))
    issues = check_article(RECAP, article(far), windows).issues
    assert [i.message for i in issues] == ["number 99 is not in the cited claims"]
    # Without the captions the same names fail, as before.
    assert not check_article(RECAP, article(near)).passed


def test_the_prompt_shows_each_claims_captions() -> None:
    from civiccast_brief.article.writer import build_prompt, caption_windows

    prompt = build_prompt(RECAP, windows=caption_windows(RECAP, _segments()))
    assert (
        '"captions": "Item 12, Ordinance O-2026-70, the Council member Waters moves approval"'
        in prompt
    )


GOOD = Paragraph("The council approved the annexation 6-1.", ("c01",))


def test_a_vote_is_checked_against_its_claim_and_dropped_when_it_adds_a_number() -> None:
    good = Vote("8513 St. Vrain Road annexation", "passed", "6-1", "c01")
    bad = Vote("8513 St. Vrain Road annexation", "passed", "7-0", "c01")
    story = replace(article(GOOD), votes=(good, bad))
    issues = check_article(RECAP, story).issues
    assert {(i.where, i.code) for i in issues} == {("vote 2", "number")}
    kept, dropped = drop_unsupported(RECAP, story)
    assert kept.votes == (good,) and dropped.votes == [bad]


def test_a_quote_takes_the_claims_words_and_its_speaker_must_be_backed() -> None:
    story = replace(article(GOOD), quotes=(Quote("c02", "A speaker"), Quote("c02", "Mayor Peck")))
    issues = check_article(RECAP, story).issues
    assert {(i.where, i.code) for i in issues} == {("quote 2", "name")}
    site = site_article(RECAP, replace(story, quotes=story.quotes[:1]))
    assert site["quotes"] == [
        {"text": "please keep Sunset pool open", "speaker": "A speaker", "start_s": 600}
    ]


def test_a_failing_dek_or_heading_is_blanked_and_the_story_then_passes() -> None:
    story = Article(
        headline="Council approves annexation",
        dek="The vote was 7-0.",
        sections=(Section(None, (GOOD,)), Section("Sunset pool closes", (GOOD,))),
    )
    kept, dropped = drop_unsupported(RECAP, story)
    assert kept.dek == "" and kept.sections[1].heading is None
    assert dropped.texts == ["Sunset pool closes", "The vote was 7-0."]
    assert check_article(RECAP, kept).passed


def test_the_site_format_cites_claim_positions_and_start_times() -> None:
    story = Article(
        headline="Council approves annexation",
        sections=(
            Section(None, (Paragraph("It passed 6-1. One speaker spoke.", ("c01", "c02")),)),
        ),
        votes=(Vote("Annexation", "passed", "6-1", "c01"),),
    )
    site = site_article(RECAP, story)
    assert site["sections"][0]["paragraphs"][0]["cites"] == [
        {"claim": 0, "start_s": 3723},
        {"claim": 1, "start_s": 600},
    ]
    assert site["votes"] == [
        {"motion": "Annexation", "result": "passed", "tally": "6-1", "start_s": 3723}
    ]
    with pytest.raises(ArticleFormatError):
        site_article(RECAP, replace(story, votes=(Vote("X", "passed", None, "c09"),)))


# --- News judgment, record names, and the weekly roundup ---------------------------------------

NEWS_RECAP = Recap(
    city="Superior",
    body="Town Council",
    meeting_date="October 26, 2026",
    video_id="abc12345678",
    claims=(
        Claim(
            id="c01",
            topic="Council resignation",
            text="A council member said they needed to resign their council seat.",
            timestamp="0:04:47",
            quote="I needed to resign my council seat.",
        ),
        Claim(
            id="c02",
            topic="Consent agenda",
            text="The council moved to approve the consent agenda items 5A through 5H.",
            timestamp="0:59:46",
            quote="motion to approve the consent agenda",
        ),
        Claim(
            id="c03",
            topic="Budget",
            text="The council approved a $2 million budget amendment 4-3.",
            timestamp="1:20:00",
            quote="the budget amendment passes four to three",
        ),
    ),
)


def test_a_resignation_and_a_money_vote_outrank_a_consent_item() -> None:
    from civiccast_brief.article.writer import news_order, news_value

    assert news_value(NEWS_RECAP.claims[0]) > news_value(NEWS_RECAP.claims[2])
    assert news_value(NEWS_RECAP.claims[2]) > news_value(NEWS_RECAP.claims[1])
    assert news_order(NEWS_RECAP)[0] == "c01"


def test_a_story_that_leads_on_consent_when_a_resignation_is_cited_is_flagged() -> None:
    from civiccast_brief.article.writer import lead_is_routine

    routine = Article(
        headline="Superior council approves consent agenda",
        sections=(
            Section(None, (Paragraph("The council approved the consent agenda.", ("c02",)),)),
        ),
    )
    assert lead_is_routine(NEWS_RECAP, routine) == "c01"
    led = Article(
        headline="Superior council member resigns seat",
        sections=(
            Section(None, (Paragraph("A council member said they would resign.", ("c01",)),)),
        ),
    )
    assert lead_is_routine(NEWS_RECAP, led) is None


def test_a_story_whose_lead_and_headline_bury_the_resignation_fails() -> None:
    buried = Article(
        headline="Superior council moves fundraising advisor agreement",
        sections=(
            Section(
                None,
                (
                    Paragraph(
                        "The council moved to approve the fundraising advisor agreement.",
                        ("c03",),
                    ),
                ),
            ),
        ),
    )
    codes = [(issue.where, issue.code) for issue in check_article(NEWS_RECAP, buried).issues]
    assert ("lead", "news_lead") in codes
    assert ("headline", "news_headline") in codes
    led = Article(
        headline="Superior council member announces resignation over day-job conflicts",
        sections=(
            Section(
                None,
                (Paragraph("A council member said they needed to resign.", ("c01",)),),
            ),
        ),
    )
    report = check_article(NEWS_RECAP, led)
    assert report.passed, report.render()


def test_a_passing_mention_of_a_contract_is_not_a_money_vote() -> None:
    from civiccast_brief.article.writer import news_value
    from civiccast_brief.script.recap import Claim

    background = Claim(
        "c09",
        "Program",
        "The Superior contract started in 2021.",
        "0:29:22",
        "our contract started in 2021",
    )
    assert news_value(background) < 60
    decision = Claim(
        "c10",
        "Budget",
        "The council approved the 2027 budget of $65.5 million.",
        "1:14:48",
        "the budget passes",
    )
    assert news_value(decision) >= 70


def test_the_prompt_lists_the_news_order_and_a_roundup_uses_the_week_prompt() -> None:
    from civiccast_brief.article.writer import build_prompt

    prompt = build_prompt(NEWS_RECAP)
    assert "NEWS JUDGMENT" in prompt
    assert "News value, strongest first" in prompt and "c01" in prompt
    roundup = build_prompt(NEWS_RECAP, roundup=True)
    assert "one story about the week" in roundup
    assert "Also this week" in roundup


def test_a_speaker_named_in_the_record_reaches_the_writer_and_the_check() -> None:
    from civiccast_brief.article.writer import build_prompt, claim_speakers
    from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster
    from civiccast_brief.meeting.transcript import Segment

    roster = load_roster(EXAMPLE_ROSTER)
    segments = [
        Segment(start=0.0, end=1.0, text="Roll call.", turn_start=False),
        Segment(start=60.0, end=62.0, text="Council member Popkin", turn_start=False),
        Segment(start=63.0, end=64.0, text="Here.", turn_start=False),
    ]
    recap = Recap(
        city="Longmont",
        body="City Council",
        meeting_date="October 20, 2026",
        video_id="uHFuCfd5MVQ",
        claims=(
            Claim(
                id="c01",
                topic="Roll call",
                text="The roll call recorded a member present.",
                timestamp="0:01:03",
                quote="Here.",
            ),
        ),
    )
    speakers = claim_speakers(recap, segments, roster)
    assert speakers == {"c01": "Matthew Popkin"}
    assert '"speaker": "Matthew Popkin"' in build_prompt(recap, speakers=speakers)
    named = Article(
        headline="Longmont council meets",
        sections=(Section(None, (Paragraph("Matthew Popkin answered the roll call.", ("c01",)),)),),
    )
    assert check_article(recap, named, speakers=speakers).passed
    # Without the record's name, the same text adds a name the evidence lacks.
    assert [i.code for i in check_article(recap, named).issues] == ["name", "name"]


def test_an_unnamed_line_keeps_a_neutral_description() -> None:
    from civiccast_brief.article.writer import claim_speakers
    from civiccast_brief.meeting.roster import EXAMPLE_ROSTER, load_roster
    from civiccast_brief.meeting.transcript import Segment

    roster = load_roster(EXAMPLE_ROSTER)
    segments = [Segment(start=0.0, end=1.0, text="I need to resign.", turn_start=False)]
    recap = Recap(
        city="Longmont",
        body="City Council",
        meeting_date="October 20, 2026",
        video_id="uHFuCfd5MVQ",
        claims=(
            Claim(
                id="c01",
                topic="Resignation",
                text="A speaker said they would resign.",
                timestamp="0:00:00",
                quote="I need to resign.",
            ),
        ),
    )
    assert claim_speakers(recap, segments, roster) == {}


def test_a_passing_story_under_the_length_floor_is_flagged_for_one_rewrite() -> None:
    from civiccast_brief.article.writer import MIN_STORY_WORDS, story_too_short, story_words

    short = Article(
        headline="Superior council approves consent agenda",
        sections=(Section(None, (Paragraph("The council approved it.", ("c02",)),)),),
    )
    assert story_words(short) == 4
    assert not story_too_short(NEWS_RECAP, short)  # only three claims to build from
    enough = replace(
        NEWS_RECAP,
        claims=NEWS_RECAP.claims
        + tuple(
            Claim(
                id=f"c{number:02d}",
                topic="Roads",
                text=f"The council approved road plan {number}.",
                timestamp=f"1:{number:02d}:00",
                quote=f"road plan {number} approved",
            )
            for number in range(4, 4 + 12)
        ),
    )
    assert story_too_short(enough, short)
    long = Article(
        headline="Superior council approves consent agenda",
        sections=(
            Section(
                None,
                tuple(
                    Paragraph("The council approved the consent agenda. " * 40, ("c02",))
                    for _ in range(4)
                ),
            ),
        ),
    )
    assert story_words(long) >= MIN_STORY_WORDS
    assert not story_too_short(enough, long)


def test_a_routine_appointment_resolution_is_not_the_meeting_news() -> None:
    from civiccast_brief.article.writer import news_value
    from civiccast_brief.script.recap import Claim

    appointment = Claim(
        "c18",
        "Appointments",
        "The Town Council considered Resolution No. R-96 making appointments to the Parks and "
        "Rec Advisory Committee.",
        "1:40:00",
        "move to approve the resolution making the appointments",
    )
    assert news_value(appointment) == 0


def test_a_short_month_is_not_a_name() -> None:
    from civiccast_brief.article.writer import _names

    assert _names("The council met Oct. 26 and again Nov. 2.") == []


# --- Who did what: the same-speaker link and a recusal the record does not name -------------


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text


def _record(text: str = "The council appointed members to the board."):
    return load_record({"d1p1": _Page(text)})


def test_the_same_speaker_link_fails_when_the_claims_have_different_speakers() -> None:
    story = article(
        Paragraph("The council approved the annexation 6-1.", ("c01",)),
        Paragraph("The same speaker also asked to keep the Sunset pool open.", ("c01", "c02")),
    )
    speakers = {"c01": "Neal Shah", "c02": "Stephanie Miller"}
    codes = [issue.code for issue in check_article(RECAP, story, speakers=speakers).issues]
    assert "same_speaker" in codes


def test_the_same_speaker_link_passes_when_the_record_names_one_speaker() -> None:
    story = article(
        Paragraph("The council approved the annexation 6-1.", ("c01",)),
        Paragraph("The same speaker also asked to keep the Sunset pool open.", ("c01", "c02")),
    )
    speakers = {"c01": "Neal Shah", "c02": "Neal Shah"}
    report = check_article(RECAP, story, speakers=speakers)
    assert "same_speaker" not in [issue.code for issue in report.issues]


def test_a_named_recusal_fails_when_the_documents_do_not_name_the_person() -> None:
    story = article(
        Paragraph("The council approved the annexation 6-1.", ("c01",)),
        Paragraph(
            "The council appointed members, with the mayor recusing himself because of a conflict.",
            ("c01",),
        ),
    )
    codes = [issue.code for issue in check_article(RECAP, story, record=_record()).issues]
    assert "recusal_name" in codes
    assert "pronoun" in codes


def test_a_neutral_recusal_passes() -> None:
    story = article(
        Paragraph("The council approved the annexation 6-1.", ("c01",)),
        Paragraph(
            "The council appointed members, with a council member recusing themselves "
            "because of a conflict.",
            ("c01",),
        ),
    )
    codes = [issue.code for issue in check_article(RECAP, story, record=_record()).issues]
    assert "recusal_name" not in codes and "pronoun" not in codes


def test_a_recusal_the_documents_name_is_allowed() -> None:
    record = _record(
        "The council appointed members. Mayor Mark Lacis recused himself from the appointment vote."
    )
    story = article(
        Paragraph("The council approved the annexation 6-1.", ("c01",)),
        Paragraph("The council appointed members, with the mayor recusing himself.", ("c01",)),
    )
    codes = [issue.code for issue in check_article(RECAP, story, record=record).issues]
    assert "recusal_name" not in codes and "pronoun" not in codes


def test_a_dollar_figure_a_claim_states_is_reported_when_the_story_leaves_it_out() -> None:
    recap = replace(
        RECAP,
        claims=(
            *RECAP.claims,
            Claim(
                id="c03",
                topic="Budget",
                text="The urban renewal authority property tax movement was $250,000.",
                timestamp="1:49:59",
                quote="two hundred fifty thousand",
            ),
        ),
    )
    short = article(Paragraph("The council approved the annexation 6-1.", ("c01",)))
    assert missing_money(recap, short) == ["$250,000 (claim c03)"]
    full = article(
        Paragraph("The council approved the annexation 6-1.", ("c01",)),
        Paragraph("The property tax movement was $250,000.", ("c03",)),
    )
    assert missing_money(recap, full) == []


def test_a_bare_result_is_not_the_meeting_news_and_does_not_lead() -> None:
    from civiccast_brief.article.writer import news_value, top_news
    from civiccast_brief.script.recap import Claim, Recap

    bare = Claim("c01", "2027 budget", "The motion carried.", "0:10:00", "the motion carried")
    assert news_value(bare) == 10
    subject = Claim(
        "c02", "2027 budget", "The council approved the 2027 budget.", "0:11:00", "approved"
    )
    assert news_value(subject) == 70
    contested = Claim(
        "c03",
        "2027 budget",
        "The motion passed 6 to 1, with one member voting no.",
        "0:12:00",
        "six to one",
    )
    assert news_value(contested) == 10
    resignation = Claim("c04", "Mayor", "Mayor Moore resigned.", "0:13:00", "I resign")
    assert news_value(resignation) == 100

    week = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="November 3, 2026",
        video_id="abc12345678",
        claims=(
            Claim("c05", "Water rates", "The item carried unanimously.", "0:05:00", "carried"),
        ),
    )
    assert top_news(week) is None


def test_speaker_statements_staff_recommendations_and_procedure_are_not_the_news() -> None:
    from civiccast_brief.article.writer import news_value

    routine = (
        (
            35,
            "Public comment: mayoral vacancy",
            (
                "An unidentified speaker argued that the town charter required the council to "
                "declare a vacancy at that night's meeting and fill it according to the "
                "charter's timelines."
            ),
            "resignations",
        ),
        (10, "Staff recommendation", "Staff recommended adopting the ordinance.", ""),
        (
            0,
            "Adjournment",
            "The board adjourned the meeting.",
            "All those in favor and all those opposed, we are adjourned.",
        ),
        (
            10,
            "Motion vote",
            "The council carried the motion by voice vote.",
            "All right, all in favor say aye. All opposed say no. Motion carries.",
        ),
        (
            10,
            "Resolution on county commission size",
            (
                "The meeting moved to consideration of a resolution supporting an increase in "
                "county commissioners from three to five."
            ),
            "",
        ),
        (
            10,
            "Resolution LHA 2026-19 amendments",
            (
                "Commissioner McCoy made amendments to section 17 that were seconded by a "
                "commissioner."
            ),
            "",
        ),
    )
    for index, (expected, topic, text, quote) in enumerate(routine):
        claim = Claim(f"r{index}", topic, text, "0:10:00", quote)
        assert news_value(claim) == expected, (topic, text)

    real = (
        (
            70,
            "Resolution 2026, 203",
            "The council carried the motion by voice vote.",
            "Aye. I'll oppose say no. Motion carries.",
        ),
        (70, "Road repair", "The council approved a $2 million contract for road repair.", ""),
        (
            60,
            "Fee increase vote",
            "Council Member Smith voted no, and the motion carried 4-1.",
            "",
        ),
        (
            60,
            "Rezoning",
            "Council Member Smith opposed the rezoning plan in a divided vote.",
            "",
        ),
        (100, "Mayor", "The mayor resigned effective December 1.", ""),
        (
            60,
            "Parking plan",
            "Council members split on the parking plan and the motion failed.",
            "",
        ),
        (35, "Public comment: parking", "A resident asked the council to add parking.", ""),
        (70, "Ordinance", "Council Member Lee moved to table the ordinance.", ""),
        (10, "Agenda", "The board moved to the next item.", ""),
    )
    for index, (expected, topic, text, quote) in enumerate(real):
        claim = Claim(f"n{index}", topic, text, "0:15:00", quote)
        assert news_value(claim) == expected, (topic, text)
