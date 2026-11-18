"""The weekly roundup built from the meetings' new stories."""

from __future__ import annotations

from civiccast_brief.article.roundup import Story, build
from civiccast_brief.article.writer import Article, Paragraph, Section
from civiccast_brief.script.recap import Claim, Recap


def _story(headline: str, paragraphs: list[str], also: str) -> Story:
    claims = tuple(
        Claim(f"c{n:02d}", "", text, f"0:0{n}:00", text) for n, text in enumerate(paragraphs, 1)
    )
    sections = (
        Section(None, tuple(Paragraph(t, (f"c{n:02d}",)) for n, t in enumerate(paragraphs, 1))),
        Section("Also at the meeting", (Paragraph(also, ("c01",)),)),
    )
    return Story(
        Article(headline=headline, dek="A dek.", sections=sections),
        Recap("Superior", "Town Council", "September 28, 2026", "v", claims),
    )


def test_the_roundup_leads_with_the_biggest_news_and_keeps_every_claim() -> None:
    code = _story(
        "Superior adopts the fire code",
        ["The council adopted the fire code.", "The code takes effect in 10 days."],
        "The council approved the consent agenda.",
    )
    resignation = _story(
        "Superior council member resigns",
        ["A council member resigned, citing job conflicts."],
        "The council approved a park contract.",
    )
    article, recap = build([code, resignation])
    assert article.headline == "Superior council member resigns"
    assert article.paragraphs[0].text == "A council member resigned, citing job conflicts."
    headings = [section.heading for section in article.sections]
    assert headings == [None, "Superior adopts the fire code", "Also this week"]
    # Every paragraph cites a claim that the merged recap holds.
    known = {claim.id for claim in recap.claims}
    assert all(set(p.claim_ids) <= known for p in article.paragraphs)
    assert len(recap.claims) == 3


def test_an_empty_week_cannot_make_a_roundup() -> None:
    try:
        build([])
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an empty roundup must be refused")
