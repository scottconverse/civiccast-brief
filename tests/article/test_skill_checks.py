"""The desk's checks on a skill-written story: the lead, names tied to acts, whole sentences,
the ALSO heading, and the evidence the page publishes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from civiccast_brief.article import skill
from civiccast_brief.article.__main__ import main, skill_desk_rules
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
    load_article,
    skill_lead_issue,
    split_sentences,
    top_news,
)
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.script.recap import Claim, Recap, save_recap

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


@pytest.mark.parametrize(
    ("ref", "wanted"),
    [
        ("page 41", ("pk41",)),
        ("pages 41 to 42", ("pk41", "pk42")),
        ("pages 41 through 42", ("pk41", "pk42")),
        ("pages 41-42", ("pk41", "pk42")),
        ("pages 41–42", ("pk41", "pk42")),
        ("p. 41", ("pk41",)),
        ("p.41", ("pk41",)),
        ("pp. 41-42", ("pk41", "pk42")),
        ("p. 41 and 42", ("pk41", "pk42")),
        ("pages 41, 42 and 45", ("pk41", "pk42", "pk45")),
    ],
)
def test_each_page_reference_the_skill_writes_names_its_packet_pages(
    ref: str, wanted: tuple[str, ...]
) -> None:
    pages = {f"pk{n}": _DocPage("packet", n) for n in range(32, 46)}
    source = f"City of Boulder agenda packet, Nov. 12, 2026, staff report, {ref}."
    assert skill._source_pages([source], pages) == wanted


def test_and_lists_separate_pages_not_a_range() -> None:
    """The reference "p. 32 and 38" means 32 and 38; 33 to 37 are not cited."""
    pages = {f"pk{n}": _DocPage("packet", n) for n in range(32, 46)}
    source = "City of Boulder agenda packet, Nov. 12, 2026, packet p. 32 and 38."
    found = skill._source_pages([source], pages)
    assert found == ("pk32", "pk38")
    assert not {f"pk{n}" for n in range(33, 38)} & set(found)


@pytest.mark.parametrize("ref", ["7 p.m.", "p.m. 5", "step 41"])
def test_a_time_or_ordinary_number_is_not_a_page_reference(ref: str) -> None:
    pages = {f"pk{n}": _DocPage("packet", n) for n in range(1, 60)}
    source = f"City of Boulder agenda packet, Nov. 12, 2026, {ref}."
    assert skill._source_pages([source], pages) == ()


@pytest.mark.parametrize(
    "source",
    [
        "Meeting packet p. 41, 1:10:24",
        "Meeting packet, page 41, 5:02:11 to 5:03:00",
    ],
)
def test_a_timestamp_after_a_page_list_names_no_page(source: str) -> None:
    """A number followed by ":" is a video time, never the next page in the list."""
    pages = {f"pk{n}": _DocPage("packet", n) for n in range(1, 60)}
    assert skill._source_pages([source], pages) == ("pk41",)


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


# ---------------------------------------------------------------- a retry keeps its own evidence

RUN_ONE_STORY = """Council votes to rezone the Frontier Avenue property

The vote was 8 to 0.

The council voted 8 to 0 to rezone the Frontier Avenue property.

The council voted 7 to 1 to annex 5.43 acres near the creek.

The council voted 9 to 1 to request an independent audit.

CLAIMS AND SOURCES

Claim: The council voted 8 to 0 to rezone the Frontier Avenue property.
Source (primary record): City Council captions, Nov. 12, 2026, 0:10:00. Supplied text.

Claim: The council voted 7 to 1 to annex 5.43 acres near the creek.
Source (primary record): City Council captions, Nov. 12, 2026, 0:20:00. Supplied text.

Claim: The council voted 6 to 2 to request an independent audit.
Source (primary record): City Council captions, Nov. 12, 2026, 0:30:00. Supplied text.

EDITOR FILE: NOT FOR PUBLICATION

CLAIM CHECK

Check 1: the audit vote.
Status: primary record.
"""

RUN_TWO_STORY = """Council receives the CFO report

The vote was 6 to 0.

The council received the budget report 6 to 0.

CLAIMS AND SOURCES

Claim: The council received the budget report 6 to 0.
Source (primary record): City Council captions, Nov. 12, 2026, 1:10:00. Supplied text.

EDITOR FILE: NOT FOR PUBLICATION

CLAIM CHECK

Check 1: the budget report.
Status: primary record.
"""


def test_a_retry_judges_each_draft_against_its_own_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run 2 restarts its claim ids at s01, so run 2's recap must not judge run 1's prose.

    Run 1 loses one paragraph to a wrong vote count; run 2 fails on a headline word ("CFO").
    The fallback must trim run 1 against run 1's claims and keep the two supported paragraphs.
    """
    recap_path = tmp_path / "recap.json"
    save_recap(
        Recap(
            city="Boulder",
            body="City Council",
            meeting_date="November 12, 2026",
            video_id="v",
            media_url="https://example.test/meeting.mp4",
            claims=(),
        ),
        recap_path,
    )
    runs = [
        skill.SkillRun(True, tmp_path / "skill", story=skill.parse_skill_story(RUN_ONE_STORY)),
        skill.SkillRun(True, tmp_path / "skill", story=skill.parse_skill_story(RUN_TWO_STORY)),
    ]
    monkeypatch.setattr(skill, "run_skill", lambda **_kwargs: runs.pop(0))

    out = tmp_path / "article"
    assert main(["skill", "--recap", str(recap_path), "--out", str(out)]) == 0

    saved = load_article(out.with_suffix(".json"))
    assert [p.text for p in saved.paragraphs] == [
        "The council voted 8 to 0 to rezone the Frontier Avenue property.",
        "The council voted 7 to 1 to annex 5.43 acres near the creek.",
    ]
    story_recap = json.loads((tmp_path / "story-recap.json").read_text(encoding="utf-8"))
    claim_texts = [claim["text"] for claim in story_recap["claims"]]
    assert "The council voted 8 to 0 to rezone the Frontier Avenue property." in claim_texts
    assert "The council received the budget report 6 to 0." not in claim_texts
    editor = out.with_suffix(".editor.txt").read_text(encoding="utf-8")
    assert "Check 1: the audit vote." in editor
    assert "Check 1: the budget report." not in editor


SEAT_ONLY_STORY = """Council votes to deny the appeal

The vote was 5 to 2.

The council voted 5 to 2 to deny the appeal, and the Mayor Pro Tem voted no.

CLAIMS AND SOURCES

Claim: The council voted 5 to 2 to deny the appeal, and the Mayor Pro Tem voted no.
Source (primary record): Town Council captions, Oct. 26, 2026, 1:00:00. Supplied text.

EDITOR FILE: NOT FOR PUBLICATION

CLAIM CHECK

Check 1: the appeal vote.
Status: primary record.
"""


def _seat_only_story_to_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    """Write the seat-only skill story, then return (city, recap, article JSON)."""
    city = tmp_path / "city"
    city.mkdir()
    (city / "roster.json").write_text(
        json.dumps(
            {
                "city": "Superior",
                "body": "Town Council",
                "state": "CO",
                "members": [
                    {"name": "Mark Lacis", "first": "Mark", "last": "Lacis", "role": "Mayor"},
                    {
                        "name": "Jenn Kaaoush",
                        "first": "Jenn",
                        "last": "Kaaoush",
                        "role": "Mayor Pro Tem",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    recap_path = tmp_path / "recap.json"
    save_recap(
        Recap(
            city="Superior",
            body="Town Council",
            meeting_date="October 26, 2026",
            video_id="civicclerk-superiorco-43",
            claims=(
                Claim(
                    "c01",
                    "Appeal",
                    "The council voted 5 to 2 to deny the appeal, and the Mayor Pro Tem voted no.",
                    "1:00:00",
                    "voted no",
                ),
            ),
        ),
        recap_path,
    )
    monkeypatch.setattr(
        skill,
        "run_skill",
        lambda **_kwargs: skill.SkillRun(
            True, tmp_path / "skill", story=skill.parse_skill_story(SEAT_ONLY_STORY)
        ),
    )
    out = tmp_path / "out" / "article"
    out.parent.mkdir()
    assert main(["skill", "--recap", str(recap_path), "--out", str(out), "--city", str(city)]) == 0
    return city, recap_path, out.with_suffix(".json")


def test_stage_keeps_the_skill_paths_rule_and_never_names_a_seat_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The weekly run writes with `skill`, then stages the same story one call later.

    Stage must not add a roster name a seat-only sentence did not have: the skill path left
    "the Mayor Pro Tem" unnamed on purpose, and naming it again would print it twice in the
    "Name, the mayor pro tem" shape the audit found in a published story.
    """
    city, recap_path, article_json = _seat_only_story_to_stage(tmp_path, monkeypatch)

    db = tmp_path / "desk.db"
    staged = main(
        [
            "stage",
            "--db",
            str(db),
            "--recap",
            str(recap_path),
            "--article",
            str(article_json),
            "--city",
            str(city),
        ]
    )
    assert staged == 0
    with ReviewStore(db) as store:
        [item] = store.list_items()
    text = " ".join(
        paragraph["text"]
        for section in item.article["sections"]
        for paragraph in section["paragraphs"]
    )
    assert "Mayor Pro Tem" in text
    assert "Kaaoush" not in text


def test_stage_review_only_keeps_a_passing_story_in_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`article stage --review-only` holds a story that passed its checks in review."""
    city, recap_path, article_json = _seat_only_story_to_stage(tmp_path, monkeypatch)

    db = tmp_path / "desk.db"
    staged = main(
        [
            "stage",
            "--db",
            str(db),
            "--recap",
            str(recap_path),
            "--article",
            str(article_json),
            "--city",
            str(city),
            "--review-only",
        ]
    )
    assert staged == 0
    with ReviewStore(db) as store:
        [item] = store.list_items()
    assert item.checks_passed is True
    assert item.status == "review"


def test_skill_writes_the_evidence_beside_the_story_not_beside_the_recap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`article skill --recap A --out B` must not overwrite A/story-recap.json.

    The evidence belongs with the article it describes; `check` then finds it beside the
    article, so a re-run into another folder cannot rewrite an older story's evidence.
    """
    recap_dir = tmp_path / "recap"
    recap_dir.mkdir()
    recap_path = recap_dir / "recap.json"
    save_recap(
        Recap(
            city="Boulder",
            body="City Council",
            meeting_date="November 12, 2026",
            video_id="v",
            media_url="https://example.test/meeting.mp4",
            claims=(),
        ),
        recap_path,
    )
    monkeypatch.setattr(
        skill,
        "run_skill",
        lambda **_kwargs: skill.SkillRun(
            True, tmp_path / "skill", story=skill.parse_skill_story(RUN_ONE_STORY)
        ),
    )
    out = tmp_path / "out" / "article"
    out.parent.mkdir()
    assert main(["skill", "--recap", str(recap_path), "--out", str(out)]) == 0

    assert (out.parent / "story-recap.json").is_file()
    assert not (recap_dir / "story-recap.json").exists()
    # The check finds the evidence beside the article it is checking.
    assert (
        main(
            [
                "check",
                "--recap",
                str(recap_path),
                "--article",
                str(out.with_suffix(".json")),
            ]
        )
        == 0
    )
