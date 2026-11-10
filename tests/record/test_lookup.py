"""The evidence lookup: finding the meeting, choosing the captions, and checking a draft.

The rows are stored through the real repository functions and artifact store. The meeting has
the shape of Longmont's October 20, 2026 council meeting in the record: its primary video has no
transcript and a second video of the same meeting has one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from civiccast_brief.record import lookup
from civiccast_brief.record.artifacts import store
from civiccast_brief.record.repo import (
    insert_agenda_item,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_segment,
    insert_source,
    insert_transcript,
    insert_video,
    insert_vote,
)

SPOKEN = "The motion to approve the consent agenda carried."


@dataclass(frozen=True)
class Rows:
    meeting: int
    other_meeting: int
    primary: int
    second: int


@pytest.fixture
def rows(conn: sqlite3.Connection, tmp_path: Path) -> Rows:
    city = insert_jurisdiction(
        conn, type="city", name="Longmont", official_id="0845970", official_id_kind="geoid"
    )
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    channel = insert_source(
        conn,
        jurisdiction_id=city,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-test",
        suggested_by="discovery",
        reason="The city's meeting page links to it.",
        status="accepted",
    )
    meeting = insert_meeting(
        conn, body_id=body, starts_at="2026-10-20T18:00:00-06:00", title="Regular Session"
    )
    other = insert_meeting(
        conn, body_id=body, starts_at="2026-10-27T18:00:00-06:00", title="Study Session"
    )
    primary = insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id="uHFuCfd5MVQ",
        title="Regular Session",
        duration_s=100,
        is_primary=True,
    )
    second = insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id="rsagM9xn28Y",
        title="Regular Session (part 2)",
        duration_s=100,
        is_primary=False,
    )
    root = tmp_path / "storage"
    root.mkdir()
    artifact = store(conn, root, "transcript", b"WEBVTT\n\n", "vtt").id
    transcript = insert_transcript(
        conn, video_id=second, artifact_id=artifact, origin="publisher_captions"
    )
    insert_segment(conn, transcript_id=transcript, start_ms=60_000, end_ms=64_000, text=SPOKEN)
    item = insert_agenda_item(conn, meeting_id=meeting, number="9.A", title="Consent agenda")
    insert_vote(
        conn,
        agenda_item_id=item,
        result="passed",
        source_kind="minutes",
        evidence="Carried: 7 - 0",
        tally={"yes": 7, "no": 0},
    )
    return Rows(meeting=meeting, other_meeting=other, primary=primary, second=second)


class TestFindMeeting:
    def test_a_video_id_finds_its_meeting(self, conn: sqlite3.Connection, rows: Rows) -> None:
        assert lookup.find_meeting(conn, video_id="rsagM9xn28Y") == rows.meeting

    def test_a_video_the_record_does_not_link_fails(
        self, conn: sqlite3.Connection, rows: Rows
    ) -> None:
        with pytest.raises(lookup.LookupError_, match="not linked"):
            lookup.find_meeting(conn, video_id="no-such-video")

    def test_a_meeting_id_not_in_the_record_fails(
        self, conn: sqlite3.Connection, rows: Rows
    ) -> None:
        with pytest.raises(lookup.LookupError_, match="no meeting 999"):
            lookup.find_meeting(conn, meeting_id=999)


class TestCaptions:
    def test_a_primary_video_without_a_transcript_falls_back_and_says_so(
        self, conn: sqlite3.Connection, rows: Rows
    ) -> None:
        captions = lookup.caption_video(conn, rows.meeting)
        assert captions.video_id == rows.second
        assert "uHFuCfd5MVQ has no transcript" in captions.note
        pack = lookup.pack_for(conn, rows.meeting, video_id=captions.video_id)
        assert [s.text for s in pack if s.kind == "video"] == [SPOKEN]

    def test_without_the_choice_the_pack_has_no_caption_lines(
        self, conn: sqlite3.Connection, rows: Rows
    ) -> None:
        # The pack on its own reads the primary video only. This is the gap the choice fills.
        assert not [s for s in lookup.pack_for(conn, rows.meeting) if s.kind == "video"]

    def test_a_meeting_with_no_video_says_so(self, conn: sqlite3.Connection, rows: Rows) -> None:
        captions = lookup.caption_video(conn, rows.other_meeting)
        assert captions.video_id is None
        assert captions.note == "The record holds no video for this meeting."


class TestDescribeAndCheck:
    def test_describe_counts_the_spans_and_names_the_caption_video(
        self, conn: sqlite3.Connection, rows: Rows
    ) -> None:
        captions = lookup.caption_video(conn, rows.meeting)
        pack = lookup.pack_for(conn, rows.meeting, video_id=captions.video_id)
        data = lookup.describe(conn, rows.meeting, pack, captions)
        assert data["counts"] == {"video": 1, "vote": 1}
        assert data["captions"]["platform_video_id"] == "rsagM9xn28Y"
        assert data["meeting"]["body"] == "City Council"
        assert [s["handle"] for s in data["spans"]] == ["[E1]", "[E2]"]

    def test_check_passes_a_cited_quote_and_fails_an_uncited_sentence(
        self, conn: sqlite3.Connection, rows: Rows
    ) -> None:
        captions = lookup.caption_video(conn, rows.meeting)
        pack = lookup.pack_for(conn, rows.meeting, video_id=captions.video_id)
        draft = f'The chair said "{SPOKEN}" [E1]. The council also cut the budget.'
        result = lookup.check(pack, draft)
        assert result["sentences"] == 2
        assert result["grounded"] == 1
        failed = [v for v in result["verdicts"] if not v["ok"]]
        assert len(failed) == 1
        assert "budget" in failed[0]["sentence"]
        assert failed[0]["code"] == "no-handle"
