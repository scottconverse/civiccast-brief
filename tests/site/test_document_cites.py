"""A fact from the meeting documents has no video moment, so it gets no 0:00:00 video link."""

from __future__ import annotations

from civiccast_brief.site import pages
from civiccast_brief.site.model import (
    Article,
    ArticleCite,
    ArticlePage,
    ArticleParagraph,
    ArticleSection,
    Claim,
)

VIDEO = "https://example.test/meeting.mp4"


def test_a_document_only_fact_gets_no_video_link() -> None:
    article = Article(
        headline="Budget adopted",
        dek="",
        sections=(
            ArticleSection(
                None,
                (
                    ArticleParagraph(
                        "The budget totals $55,890,915.",
                        (ArticleCite(0, 0.0), ArticleCite(1, 312.0)),
                        (ArticlePage("packet", "https://example.test/packet.pdf", 4),),
                    ),
                ),
            ),
        ),
        votes=(),
        quotes=(),
        topics=(),
    )
    view = pages._article_view(article, VIDEO)
    cites = view["sections"][0]["paragraphs"][0]["cites"]
    assert [c["timestamp"] for c in cites] == ["0:05:12"]
    assert "#t=0" not in str(cites)
    claims = pages._claims_view(
        (Claim("s01", "the story's own claim", "From the packet.", 0.0, ""),), VIDEO
    )
    assert claims[0]["link"] is None and claims[0]["timestamp"] is None
