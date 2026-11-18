"""The article check: every paragraph cites claims, and adds no number or name they lack."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

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
    }


def test_the_headline_is_checked_against_the_claims_the_article_cites() -> None:
    report = check_article(
        RECAP,
        article(
            Paragraph("The council approved the annexation 6-1.", ("c01",)),
            headline="Council closes Sunset pool",
        ),
    )
    assert [(i.where, i.code) for i in report.issues] == [("headline", "name")]


def test_one_speaker_is_not_a_count_but_one_ordinance_is() -> None:
    passes = Paragraph("One speaker asked to keep the Sunset pool open.", ("c02",))
    fails = Paragraph("One ordinance asked to keep the Sunset pool open.", ("c02",))
    assert check_article(RECAP, article(passes)).passed
    assert [i.code for i in check_article(RECAP, article(fails)).issues] == ["number"]


def test_a_common_word_that_opens_a_sentence_is_not_a_name_but_a_name_still_is() -> None:
    text = "During public comment, one speaker asked to keep the Sunset pool open."
    assert check_article(RECAP, article(Paragraph(text, ("c02",)))).passed
    named = "During public comment, Waters asked to keep the Sunset pool open."
    issues = check_article(RECAP, article(Paragraph(named, ("c02",)))).issues
    assert [i.message for i in issues] == ["name 'Waters' is not in the cited claims"]


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
    near = Paragraph("Waters moved Ordinance O-2026-70; it passed 6-1.", ("c01",))
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
    five = replace(
        NEWS_RECAP,
        claims=NEWS_RECAP.claims
        + (
            Claim(
                id="c04",
                topic="Roads",
                text="The council approved a road plan.",
                timestamp="1:30:00",
                quote="road plan approved",
            ),
            Claim(
                id="c05",
                topic="Parks",
                text="The council approved a park plan.",
                timestamp="1:40:00",
                quote="park plan approved",
            ),
        ),
    )
    assert story_too_short(five, short)
    long = Article(
        headline="Superior council approves consent agenda",
        sections=(
            Section(
                None,
                tuple(
                    Paragraph("The council approved the consent agenda. " * 20, ("c02",))
                    for _ in range(4)
                ),
            ),
        ),
    )
    assert story_words(long) >= MIN_STORY_WORDS
    assert not story_too_short(five, long)
