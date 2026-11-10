"""A story may cite the meeting's document pages: the check then looks in those pages too.

The pages are the real draft minutes of Longmont's September 8, 2026 regular session, from
the September 22 packet (pages 17-38).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from civiccast_brief.article import __main__ as cli
from civiccast_brief.article.writer import (
    NOTE,
    NOTE_DOCUMENTS,
    Article,
    ArticleFormatError,
    Paragraph,
    Section,
    article_from_dict,
    article_to_dict,
    build_prompt,
    check_article,
    load_documents,
    pick_pages,
    published_dict,
    render_markdown,
    site_article,
    write_article,
)
from civiccast_brief.script.recap import Claim, Recap
from civiccast_brief.weekly.documents import pdf_pages

PACKET = (
    Path(__file__).resolve().parents[1]
    / "record"
    / "evidence"
    / "primegov"
    / "longmont-packet-16823-minutes-sep08-p17-38.pdf"
)
URL = "https://longmont.primegov.com/Public/CompiledDocument?meetingTemplateId=16823&compileOutputType=1"

RECAP = Recap(
    city="Longmont",
    body="City Council",
    meeting_date="September 8, 2026",
    video_id="3qfQAkAAC9U",
    claims=(
        Claim(
            id="c01",
            topic="Consent agenda",
            text="The council approved a larger water tap for the Hygiene fire house.",
            timestamp="0:41:10",
            quote="the consent agenda passes",
        ),
    ),
)
#: Page 24 of the packet: O-2026-59, moved by Alex Kalkhofer, carried 7-0.
TEXT = "Ordinance O-2026-59, moved by Alex Kalkhofer, approved a larger water tap 7-0."


@pytest.fixture(scope="module")
def documents(tmp_path_factory) -> Path:
    texts = pdf_pages(PACKET.read_bytes())
    data = {
        "format": 1,
        "documents": [
            {
                "kind": "minutes",
                "url": URL,
                "sha256": "0" * 64,
                "draft": True,
                "pages": [{"page": n, "text": t} for n, t in enumerate(texts, start=17)],
            }
        ],
    }
    path = tmp_path_factory.mktemp("meeting") / "documents.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def story(*page_ids: str) -> Article:
    return Article(
        headline="Council approves Hygiene fire house water tap",
        sections=(Section(None, (Paragraph(TEXT, ("c01",), page_ids),)),),
    )


def test_a_number_and_name_from_the_cited_page_pass_and_fail_without_it(documents) -> None:
    pages = load_documents(documents)
    assert pages["d1p24"].page == 24 and "O-2026-59" in pages["d1p24"].text

    assert check_article(RECAP, story("d1p24"), pages=pages).passed
    failed = check_article(RECAP, story(), pages=pages)
    assert {i.code for i in failed.issues} == {"number", "name"}
    # A page that does not say it does not back it either.
    wrong = check_article(RECAP, story("d1p17"), pages=pages)
    assert "number" in {i.code for i in wrong.issues}


def test_a_page_the_documents_lack_fails(documents) -> None:
    pages = load_documents(documents)
    report = check_article(RECAP, story("d1p99"), pages=pages)
    assert "unknown_page" in {i.code for i in report.issues}
    assert "unknown_page" in {i.code for i in check_article(RECAP, story("d1p24")).issues}


def test_the_site_and_markdown_link_the_page_and_the_note_names_the_documents(documents) -> None:
    pages = load_documents(documents)
    site = site_article(RECAP, story("d1p24"), pages)
    assert site["sections"][0]["paragraphs"][0]["pages"] == [
        {"kind": "minutes", "url": URL, "page": 24}
    ]
    assert "pages" not in site_article(RECAP, story(), pages)["sections"][0]["paragraphs"][0]
    markdown = render_markdown(RECAP, story("d1p24"), pages)
    assert f"[minutes p. 24]({URL}#page=24)" in markdown
    assert NOTE_DOCUMENTS in markdown and NOTE not in markdown
    assert NOTE in render_markdown(RECAP, story(), pages)
    published = published_dict(RECAP, story("d1p24"), pages)
    assert published["note"] == NOTE_DOCUMENTS
    with pytest.raises(ArticleFormatError):
        site_article(RECAP, story("d1p99"), pages)


def test_the_page_ids_survive_the_desk_round_trip() -> None:
    assert article_from_dict(article_to_dict(story("d1p24"))).sections[0].paragraphs[
        0
    ].page_ids == ("d1p24",)
    assert "pages" not in article_to_dict(story())["sections"][0]["paragraphs"][0]


def test_the_writer_is_shown_the_pages_that_match_the_claims(documents) -> None:
    pages = load_documents(documents)
    claim = Claim(
        id="c01",
        topic="Airport",
        text="The council tabled O-2026-61, the Vance Brand airport hangar lease.",
        timestamp="0:50:00",
        quote="move to table",
    )
    recap = replace(RECAP, claims=(claim,))
    shown = pick_pages(recap, pages, limit=3)
    assert shown[0].id in {"d1p24", "d1p25"}
    prompt = build_prompt(recap, pages=shown)
    assert '"id": "d1p24"' in prompt or '"id": "d1p25"' in prompt
    assert "Document pages:" in prompt and '"pages"' in prompt
    assert "Document pages:" not in build_prompt(recap)

    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return json.dumps(
            {
                "headline": "Council tables airport hangar lease",
                "sections": [
                    {
                        "paragraphs": [
                            {"text": "The council tabled O-2026-61.", "claim_ids": ["c01"], "pages": [shown[0].id]}
                        ]
                    }
                ],
            }
        )  # fmt: skip

    written = write_article(recap, complete, pages=pages)
    assert written.cited_pages == (shown[0].id,)
    assert "Document pages:" in seen[0]


def test_check_and_stage_read_documents_json_next_to_the_recap(
    documents, tmp_path, monkeypatch
) -> None:
    folder = documents.parent
    (folder / "recap.json").write_text(
        json.dumps(
            {
                "city": RECAP.city,
                "body": RECAP.body,
                "meeting_date": RECAP.meeting_date,
                "video_id": RECAP.video_id,
                "claims": [
                    {
                        "id": "c01",
                        "topic": "Consent agenda",
                        "text": RECAP.claims[0].text,
                        "timestamp": "0:41:10",
                        "quote": "the consent agenda passes",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    article_path = tmp_path / "article.json"
    article_path.write_text(json.dumps(article_to_dict(story("d1p24"))), encoding="utf-8")
    args = ["check", "--recap", str(folder / "recap.json"), "--article", str(article_path)]
    assert cli.main(args) == 0
    # The same story checked against documents that lack the page fails.
    empty = tmp_path / "empty-documents.json"
    empty.write_text(json.dumps({"format": 1, "documents": []}), encoding="utf-8")
    assert cli.main([*args, "--documents", str(empty)]) == 1
