"""Announcements get one short paragraph in the Also section, never a section of their own."""

from __future__ import annotations

from civiccast_brief.article.writer import (
    Article,
    Paragraph,
    Section,
    condense_announcements,
    merge_announcements,
)


def test_an_announcement_section_moves_into_also_on_the_agenda() -> None:
    article = Article(
        headline="Council meets",
        sections=(
            Section(None, (Paragraph("The lead.", ("c01",)),)),
            Section(
                "Businesses urged to join holiday programs",
                (Paragraph("The deadline was November 1.", ("c02",)),),
            ),
            Section("Also on the agenda", (Paragraph("Consent items passed.", ("c03",)),)),
        ),
    )
    merged, changes = merge_announcements(article)
    headings = [section.heading for section in merged.sections]
    assert "Businesses urged to join holiday programs" not in headings
    also = next(s for s in merged.sections if s.heading == "Also on the agenda")
    assert [p.text for p in also.paragraphs] == [
        "Consent items passed.",
        "The deadline was November 1.",
    ]
    assert changes


def test_no_announcement_section_leaves_the_story_alone() -> None:
    article = Article(
        headline="Council meets",
        sections=(Section(None, (Paragraph("The lead.", ("c01",)),)),),
    )
    merged, changes = merge_announcements(article)
    assert merged is article
    assert changes == []


def test_an_announcement_appears_once_in_one_sentence() -> None:
    article = Article(
        headline="Council meets",
        sections=(
            Section(None, (Paragraph("The lead.", ("c01",)),)),
            Section(
                "Also on the agenda",
                (
                    Paragraph(
                        "The cemetery would be open Saturday for a tombstone tour. "
                        "Residents were reminded the cemetery would be open Saturday for a "
                        "tombstone tour. Items 3c and 3f were pulled from the consent agenda.",
                        ("c02", "c03"),
                    ),
                ),
            ),
        ),
    )
    out, changes = condense_announcements(article)
    also = next(s for s in out.sections if s.heading == "Also on the agenda")
    text = also.paragraphs[0].text
    assert text.count("tombstone tour") == 1
    assert "Items 3c and 3f were pulled from the consent agenda." in text
    assert changes


def test_a_garbled_caption_phrase_is_dropped() -> None:
    article = Article(
        headline="Council meets",
        sections=(
            Section(
                None,
                (
                    Paragraph(
                        "The celebration was Friday night. "
                        "People should read the Superior sample for accurate dates.",
                        ("c01",),
                    ),
                ),
            ),
        ),
    )
    out, changes = condense_announcements(article)
    assert "sample for accurate dates" not in out.paragraphs[0].text
    assert changes


def test_a_name_repaired_into_a_meaningless_phrase_is_dropped() -> None:
    article = Article(
        headline="Council meets",
        sections=(
            Section(
                None,
                (
                    Paragraph(
                        "The council approved its agenda. A speaker and the a speaker were "
                        "scheduled for Sunday. A training with a speaker staff was planned.",
                        ("c01",),
                    ),
                ),
            ),
        ),
    )
    out, changes = condense_announcements(article)
    assert "the a speaker" not in out.paragraphs[0].text
    assert "a speaker staff" not in out.paragraphs[0].text
    assert "The council approved its agenda." in out.paragraphs[0].text
    assert len(changes) == 2


def test_a_roundup_also_section_holds_real_items_not_announcements() -> None:
    article = Article(
        headline="Week in Superior",
        sections=(
            Section(None, (Paragraph("The lead.", ("m1-c01",)),)),
            Section(
                "Also this week",
                (
                    Paragraph(
                        "The cemetery would be open Saturday for a tombstone tour. "
                        "Items 3c and 3f were pulled from the consent agenda.",
                        ("m1-c02", "m2-c01"),
                    ),
                ),
            ),
        ),
    )
    out, changes = condense_announcements(article, roundup=True)
    also = next(s for s in out.sections if s.heading == "Also this week")
    assert "tombstone" not in also.paragraphs[0].text
    assert "Items 3c and 3f" in also.paragraphs[0].text
    assert changes


def test_a_headline_outrunning_the_kept_story_falls_back_to_the_lead_claim() -> None:
    from civiccast_brief.article.writer import drop_unsupported
    from civiccast_brief.script.recap import Claim, Recap

    recap = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="September 28, 2026",
        video_id="civicclerk-superiorco-42",
        claims=(
            Claim("c01", "General", "The council opened a public hearing.", "0:48:31", "opened"),
            Claim("c02", "General", "The contract started in 2021.", "0:29:22", "started in 2021"),
        ),
    )
    article = Article(
        headline="Program reports growth since 2021 contract",
        sections=(Section(None, (Paragraph("The council opened a public hearing.", ("c01",)),)),),
    )
    fixed, dropped = drop_unsupported(recap, article)
    assert fixed.headline == "The council opened a public hearing."
    assert dropped.texts


def test_a_vote_with_no_subject_is_not_printable() -> None:
    from civiccast_brief.article.writer import Vote, _vote_has_no_subject

    assert _vote_has_no_subject(
        Vote("A motion received a second and passed unanimously.", "passed", None, "c01")
    )
    assert _vote_has_no_subject(Vote("Motion", "passed", None, "c01"))
    assert not _vote_has_no_subject(
        Vote("Consent agenda items 5A through 5H", "passed", None, "c01")
    )
