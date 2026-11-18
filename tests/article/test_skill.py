"""The report-local-news skill runner: the transcript, the assignment, and its answer."""

from __future__ import annotations

from civiccast_brief.article import skill
from civiccast_brief.script.recap import Claim, Recap

STORY = """Superior adopts the 2024 International Fire Code

The vote was 6-0 and the code takes effect in 10 days.

The Town Council adopted the 2024 International Fire Code by reference on second reading.

No one spoke at the public hearing.

ALSO AT THE MEETING

The council approved the consent agenda 6-0.

CLAIMS AND SOURCES

Claim: The Town Council adopted the 2024 International Fire Code by reference on second reading.
Source (primary record): Town of Superior packet, Sept. 28, 2026, item 6.a, pages 109 to 111. Supplied text.
Source (primary record): Town Council captions, Sept. 28, 2026, 0:50:13. Supplied text.

Claim: The vote was 6-0 and the code takes effect in 10 days.
Source (primary record): Town of Superior packet, Sept. 28, 2026, item 6.a, page 118. Supplied text.

Claim: No one spoke at the public hearing.
Source (primary record): Town Council captions, Sept. 28, 2026, 0:49:40. Supplied text.

Claim: The council approved the consent agenda 6-0.
Source (primary record): Town Council captions, Sept. 28, 2026, 0:47:18. Supplied text.

EDITOR FILE: NOT FOR PUBLICATION

CLAIM CHECK

Check 1: the code.
Status: primary record.
"""


def test_vendored_skill_is_pinned() -> None:
    assert skill.skill_version() == "3.1"
    assert skill.skill_missing() == []
    assert skill.skill_pin_ok() is True


def test_transcript_has_a_stamp_on_every_line() -> None:
    text = skill.format_transcript(
        "Town Council Meeting",
        "September 28, 2026",
        "https://example.test/meeting.mp4",
        [skill.TranscriptLine(19.0, "Thank you."), skill.TranscriptLine(20167.0, "Passes.")],
    )
    assert "0:00:19  Thank you." in text
    assert "5:36:07  Passes." in text
    assert "Full captions, 2 lines, 0:00:19 to 5:36:07." in text


def test_the_assignment_forbids_a_web_search() -> None:
    prompt = skill.assignment_prompt(
        {
            "title": "Town Council Meeting",
            "date": "September 28, 2026",
            "today": "Wednesday, Nov. 18, 2026",
            "line_count": "1,708",
            "first_stamp": "0:00:19",
            "last_stamp": "1:55:26",
        }
    )
    assert "Do not run any web search" in prompt
    assert "transcript.txt" in prompt
    assert "minutes-votes.txt" in prompt
    assert "python scripts/audit.py story.txt --meeting" in prompt


def test_story_splits_publish_from_editor() -> None:
    story = skill.parse_skill_story(STORY)
    assert story.headline == "Superior adopts the 2024 International Fire Code"
    assert story.dek == "The vote was 6-0 and the code takes effect in 10 days."
    assert "CLAIMS AND SOURCES" not in story.body
    assert "EDITOR FILE" not in story.body
    assert "ALSO AT THE MEETING" in story.body
    assert story.claims.startswith("CLAIMS AND SOURCES")
    assert story.editor_file.startswith("EDITOR FILE: NOT FOR PUBLICATION")


def test_claims_carry_their_timestamps_and_pages() -> None:
    story = skill.parse_skill_story(STORY)
    claims = skill.parse_claims(story.claims)
    assert len(claims) == 4
    assert claims[0].timestamp == "0:50:13"
    assert claims[1].timestamp == "0:00:00"


def test_a_missing_editor_marker_is_refused() -> None:
    try:
        skill.parse_skill_story("A headline\n\nA story.")
    except skill.SkillStoryError as exc:
        assert "EDITOR FILE" in str(exc)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError("a story with no editor marker must be refused")


def test_story_maps_to_the_recap_and_its_pages() -> None:
    recap = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="September 28, 2026",
        video_id="civicclerk-superiorco-42",
        claims=(
            Claim("c01", "", "The council adopted the fire code.", "0:48:12", "adopted"),
            Claim("c02", "", "The consent agenda passed.", "0:47:18", "passed"),
        ),
    )
    story = skill.parse_skill_story(STORY)
    article, claims = skill.article_from_skill(story, recap, [])
    assert len(claims) == 4
    assert article.headline == "Superior adopts the 2024 International Fire Code"
    paragraphs = article.paragraphs
    assert all(p.claim_ids for p in paragraphs)

    # The claim about pages 109 to 111 cites those pages only when the documents hold them.
    class Page:
        def __init__(self, kind: str, page: int, text: str) -> None:
            self.kind, self.page, self.text = kind, page, text

    pages = {
        "d2p109": Page("packet", 109, "International Fire Code"),
        "d2p110": Page("packet", 110, "second reading"),
        "d2p111": Page("packet", 111, "adopted"),
    }
    article, _ = skill.article_from_skill(story, recap, [], pages)
    assert "d2p109" in article.paragraphs[0].page_ids
    assert "d2p111" in article.paragraphs[0].page_ids


def test_documents_text_labels_every_page() -> None:
    text = skill.documents_text(
        {
            "documents": [
                {
                    "kind": "minutes",
                    "url": "https://example.test/m.pdf",
                    "pages": [{"page": 3, "text": "Motion: ..."}],
                }
            ]
        }
    )
    assert "=== minutes, page 3 ===" in text
    assert "https://example.test/m.pdf" in text
