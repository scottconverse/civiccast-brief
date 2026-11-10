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
