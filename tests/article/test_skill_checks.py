"""The desk's checks on a skill-written story: the lead, names tied to acts, whole sentences,
the ALSO heading, and the evidence the page publishes."""

from __future__ import annotations

from civiccast_brief.article import skill
from civiccast_brief.article.__main__ import skill_desk_rules
from civiccast_brief.article.terms import load_record
from civiccast_brief.article.writer import (
    Article,
    Paragraph,
    Quote,
    Section,
    Vote,
    check_article,
    cited_recap,
    condense_announcements,
    drop_unsupported,
    skill_lead_issue,
    split_sentences,
    top_news,
)
from civiccast_brief.script.recap import Claim, Recap

RECAP = Recap(
    city="Superior",
    body="Town Council",
    meeting_date="October 26, 2026",
    video_id="civicclerk-superiorco-43",
    claims=(
        Claim(
            "c01",
            "Council vacancy",
            "A council member said they needed to resign their council seat.",
            "0:04:47",
            "I needed to resign my council seat",
        ),
        Claim(
            "c02",
            "Budget",
            "The council adopted the 2027 budget 6-0.",
            "1:10:00",
            "the motion carries",
        ),
        Claim(
            "c03",
            "Board applicants",
            "One applicant withdrew her application before the interviews.",
            "0:30:00",
            "withdrew",
        ),
    ),
)


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text


def _record(text: str = "The council adopted the 2027 budget."):
    return load_record({"d1p1": _Page(text)})


def _story(*texts: str, headline: str = "Superior adopts 2027 budget") -> Article:
    return Article(
        headline=headline,
        sections=(Section(None, tuple(Paragraph(t, ("c02",)) for t in texts)),),
    )


def _codes(story: Article, record=None) -> list[str]:
    report = check_article(RECAP, story, record=record or _record(), skill_evidence=True)
    return [issue.code for issue in report.issues]


# ---------------------------------------------------------------- lead


def test_a_resignation_is_top_news_and_an_applicant_withdrawal_is_not() -> None:
    assert top_news(RECAP) == "c01"
    only_withdrawal = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="September 28, 2026",
        video_id="v",
        claims=(RECAP.claims[2], RECAP.claims[1]),
    )
    assert top_news(only_withdrawal) != "c03"


def test_a_skill_story_that_buries_the_resignation_fails_the_lead_check() -> None:
    story = _story(
        "The Superior Town Council adopted its 2027 budget 6-0.",
        "Later, a council member said they would resign their seat.",
    )
    assert skill_lead_issue(RECAP, story) is not None
    assert "news_lead" in _codes(story)


def test_a_skill_story_with_the_resignation_in_the_lead_passes_the_lead_check() -> None:
    story = _story(
        "A Superior Town Council member said they will resign their seat, and the council "
        "adopted its 2027 budget 6-0.",
        headline="Superior council member resigns; 2027 budget adopted",
    )
    assert skill_lead_issue(RECAP, story) is None
    assert "news_lead" not in _codes(story)


def test_the_desk_rules_name_the_top_news_for_the_skill() -> None:
    rules = skill_desk_rules(RECAP)
    assert "needed to resign their council seat" in rules
    assert "a council member" in rules
    assert "same speaker" in rules


# ---------------------------------------------------------------- names tied to acts


def test_a_named_resignation_the_documents_do_not_back_fails() -> None:
    story = _story(
        "A council member will resign, and the council adopted its 2027 budget 6-0.",
        "Council member Mike Foster resigned his seat because of his day job.",
        headline="Council member resigns",
    )
    assert "act_name" in _codes(story)


def test_a_named_not_running_again_the_documents_do_not_back_fails() -> None:
    story = _story(
        "A council member will resign, and the council adopted its 2027 budget 6-0.",
        "Council member Jenn Kaaoush said she decided not to run again.",
        headline="Council member resigns",
    )
    assert "act_name" in _codes(story)


def test_a_neutral_resignation_passes() -> None:
    story = _story(
        "A council member said they will resign their seat because of a conflict with their "
        "day job, and the council adopted its 2027 budget 6-0.",
        headline="Council member resigns",
    )
    codes = _codes(story)
    assert "act_name" not in codes and "news_lead" not in codes


def test_a_named_act_the_documents_state_passes() -> None:
    record = _record("Mayor Mark Lacis is not running again. The council adopted the budget.")
    story = _story(
        "A council member will resign, and the council adopted its 2027 budget 6-0.",
        "Mayor Mark Lacis is not running again.",
        headline="Council member resigns",
    )
    assert "act_name" not in _codes(story, record)


def test_a_named_farewell_without_an_act_passes() -> None:
    story = _story(
        "A council member will resign, and the council adopted its 2027 budget 6-0.",
        "Mayor Mark Lacis closed out 10 years on the council, the last four as mayor.",
        headline="Council member resigns",
    )
    assert "act_name" not in _codes(story)


# ---------------------------------------------------------------- whole sentences


def test_split_sentences_keeps_dates_and_times_whole() -> None:
    text = (
        "Polls close at 7 p.m. Nov. 3 and the drop box sits outside Town Hall. "
        "They listed a reception Nov. 12 and a Winter Festival on Dec. 12. "
        "The council will not meet in November."
    )
    assert split_sentences(text) == [
        "Polls close at 7 p.m. Nov. 3 and the drop box sits outside Town Hall.",
        "They listed a reception Nov. 12 and a Winter Festival on Dec. 12.",
        "The council will not meet in November.",
    ]


def test_condensing_announcements_does_not_cut_a_sentence_at_a_month() -> None:
    line = "They listed a reception Nov. 12, a Winter Festival on Dec. 12 and a commemoration."
    story = Article(
        headline="Budget adopted",
        sections=(
            Section(None, (Paragraph("The council adopted its 2027 budget 6-0.", ("c02",)),)),
            Section("ALSO AT THE MEETING", (Paragraph(line, ("c02",)),)),
        ),
    )
    condensed, _ = condense_announcements(story)
    assert any(line in p.text for p in condensed.paragraphs)


# ---------------------------------------------------------------- ALSO heading

STORY = """Superior adopts its 2027 budget

The vote was 6-0.

The Town Council adopted its 2027 budget.

ALSO AT THE MEETING

The council approved the consent agenda 6-0.

CLAIMS AND SOURCES

Claim: The Town Council adopted its 2027 budget.
Source (primary record): Town Council captions, Oct. 26, 2026, 1:10:00. Supplied text.

Claim: The council approved the consent agenda 6-0.
Source (primary record): Town Council captions, Oct. 26, 2026, 0:47:18. Supplied text.

EDITOR FILE: NOT FOR PUBLICATION

CLAIM CHECK

Check 1: the budget.
Status: primary record.
"""


def test_the_also_heading_is_a_heading_and_the_last_paragraph_is_kept() -> None:
    article, _ = skill.article_from_skill(skill.parse_skill_story(STORY), RECAP, [])
    texts = [p.text for p in article.paragraphs]
    assert not any("ALSO AT THE MEETING" in t for t in texts)
    assert texts[-1] == "The council approved the consent agenda 6-0."
    assert (article.sections[-1].heading or "").upper() == "ALSO AT THE MEETING"


# ---------------------------------------------------------------- published evidence


def test_the_published_evidence_is_only_what_the_story_cites() -> None:
    story = Article(
        headline="Budget adopted",
        sections=(
            Section(None, (Paragraph("The council adopted its 2027 budget 6-0.", ("c02",)),)),
        ),
        votes=(Vote("2027 budget", "passed", "6-0", "c02"),),
        quotes=(Quote("c01", "a council member"),),
    )
    kept = cited_recap(RECAP, story)
    assert [c.id for c in kept.claims] == ["c01", "c02"]


# ---------------------------------------------------------------- document pages


class _DocPage:
    def __init__(self, kind: str, page: int) -> None:
        self.kind = kind
        self.page = page


def test_an_agenda_packet_source_cites_the_packet_pages() -> None:
    pages = {
        "d1p53": _DocPage("agenda", 53),
        "d2p53": _DocPage("packet", 53),
        "d2p54": _DocPage("packet", 54),
    }
    source = (
        "Town of Superior agenda packet, Oct. 26, 2026, item 3.d, staff report, pages 53 to 54."
    )
    assert skill._source_pages([source], pages) == ("d2p53", "d2p54")


# ---------------------------------------------------------------- evidence that is there

BUDGET = Recap(
    city="Superior",
    body="Town Council",
    meeting_date="October 12, 2026",
    video_id="v",
    claims=(
        Claim(
            "c01",
            "Budget",
            "The budget totals $41,321,246 for 2027. The trail runs to High Plains Dr.",
            "0:10:00",
            "the budget",
        ),
    ),
)


def _evidence_codes(text: str) -> list[str]:
    story = Article(headline="Budget", sections=(Section(None, (Paragraph(text, ("c01",)),)),))
    report = check_article(BUDGET, story, skill_evidence=True)
    return [f"{issue.code}:{issue.message}" for issue in report.issues]


def test_a_rounded_budget_figure_is_the_cited_amount() -> None:
    assert not [
        c for c in _evidence_codes("Members adopted a $41.3 million budget.") if "number" in c
    ]
    assert any("number" in c for c in _evidence_codes("Members adopted a $42.3 million budget."))


def test_a_spelled_out_street_name_matches_the_record_abbreviation() -> None:
    assert not [c for c in _evidence_codes("The trail runs to High Plains Drive.") if "Drive" in c]


def test_asked_opening_a_sentence_is_not_a_name() -> None:
    codes = _evidence_codes("Asked about the budget, staff said it totals $41,321,246 for 2027.")
    assert not [c for c in codes if "'Asked'" in c]


def test_an_unsourced_last_sentence_does_not_drop_the_sourced_ones() -> None:
    story = Article(
        headline="Budget",
        sections=(
            Section(
                None,
                (
                    Paragraph(
                        "The budget totals $41,321,246 for 2027. "
                        "A member of the Quorbex subcommittee backed it.",
                        ("c01",),
                    ),
                ),
            ),
        ),
    )
    kept, dropped = drop_unsupported(BUDGET, story, skill_evidence=True)
    assert [p.text for p in kept.paragraphs] == ["The budget totals $41,321,246 for 2027."]
    assert not dropped.paragraphs
