"""Announcements get one short paragraph in the Also section, never a section of their own."""

from __future__ import annotations

from civiccast_brief.article.writer import (
    Article,
    Paragraph,
    Section,
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


def test_a_vote_with_no_subject_is_not_printable() -> None:
    from civiccast_brief.article.writer import Vote, _vote_has_no_subject

    assert _vote_has_no_subject(
        Vote("A motion received a second and passed unanimously.", "passed", None, "c01")
    )
    assert _vote_has_no_subject(Vote("Motion", "passed", None, "c01"))
    assert not _vote_has_no_subject(
        Vote("Consent agenda items 5A through 5H", "passed", None, "c01")
    )
