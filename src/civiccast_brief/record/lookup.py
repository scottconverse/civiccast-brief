"""Evidence lookup: everything the record holds for one meeting, and the check on a draft.

The writer of an article (and a person at the editor desk) asks two things of the record:

* what is there for this meeting: the timed caption lines, the pages of its agenda, packet and
  minutes, and the stored votes, each with a handle like ``[E12]`` and the SHA-256 of the file
  it came from;
* does this draft stand on it: every sentence must cite a handle, quote it word for word, and
  print no number or name the cited evidence does not hold.

Both answers come from :mod:`civiccast_brief.record.grounding` (copied from TownRecord). This
module finds the meeting, by its record id or by the YouTube video id, and turns the pack into
plain data. It reads the database and never writes to it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .grounding import EvidencePack, check_answer, evidence_pack
from .repo import get_body, get_meeting, get_video, latest_transcript, videos_of_platform


class LookupError_(LookupError):
    """The meeting asked for is not in the record."""


def find_meeting(
    conn: sqlite3.Connection, *, meeting_id: int | None = None, video_id: str | None = None
) -> int:
    """The record id of the meeting, from its id or from the id of its video."""
    if meeting_id is not None:
        if get_meeting(conn, meeting_id) is None:
            raise LookupError_(f"no meeting {meeting_id} in the record")
        return meeting_id
    if video_id:
        linked = sorted({v.meeting_id for v in videos_of_platform(conn, video_id) if v.meeting_id})
        if not linked:
            raise LookupError_(f"video {video_id} is not linked to a meeting in the record")
        if len(linked) > 1:
            raise LookupError_(f"video {video_id} is linked to meetings {linked}; give --meeting")
        return linked[0]
    raise LookupError_("give a meeting id or a video id")


@dataclass(frozen=True)
class Captions:
    """Which video's timed lines the pack holds, and why that one."""

    video_id: int | None
    platform_video_id: str | None
    note: str


def caption_video(conn: sqlite3.Connection, meeting_id: int, asked: str | None = None) -> Captions:
    """Pick the video whose captions go in the pack.

    The asked-for video comes first, then the primary video, then any other video of the
    meeting. A video counts only when the record holds a transcript for it. When the one
    asked for or the primary has none, the note says which video was used instead.
    """
    rows = conn.execute(
        "SELECT id FROM videos WHERE meeting_id = ? ORDER BY is_primary DESC, id", (meeting_id,)
    ).fetchall()
    videos = [v for v in (get_video(conn, row[0]) for row in rows) if v is not None]
    if asked:
        videos.sort(key=lambda v: v.platform_video_id != asked)
    if not videos:
        return Captions(None, None, "The record holds no video for this meeting.")
    first = videos[0]
    for video in videos:
        if latest_transcript(conn, video.id) is None:
            continue
        if video.id == first.id:
            return Captions(
                video.id, video.platform_video_id, f"Captions of {video.platform_video_id}."
            )
        return Captions(
            video.id,
            video.platform_video_id,
            f"{first.platform_video_id} has no transcript in the record; "
            f"the captions are from {video.platform_video_id}, another video of this meeting.",
        )
    return Captions(None, None, "No video of this meeting has a transcript in the record.")


def pack_for(
    conn: sqlite3.Connection, meeting_id: int, limit: int | None = None, video_id: int | None = None
) -> EvidencePack:
    return evidence_pack(conn, meeting_id=meeting_id, limit=limit, video_id=video_id)


def describe(
    conn: sqlite3.Connection,
    meeting_id: int,
    pack: EvidencePack,
    captions: Captions | None = None,
) -> dict[str, Any]:
    """The meeting and its evidence as plain data (for JSON)."""
    meeting = get_meeting(conn, meeting_id)
    assert meeting is not None
    body = get_body(conn, meeting.body_id)
    counts: dict[str, int] = {}
    for span in pack:
        counts[span.kind] = counts.get(span.kind, 0) + 1
    return {
        "meeting": {
            "id": meeting.id,
            "title": meeting.title,
            "starts_at": meeting.starts_at,
            "body": body.name if body else None,
        },
        "summary": pack.sentence(),
        "captions": (
            None
            if captions is None
            else {
                "video_id": captions.video_id,
                "platform_video_id": captions.platform_video_id,
                "note": captions.note,
            }
        ),
        "counts": counts,
        "dropped": pack.dropped,
        "roster": [o.name for o in pack.roster.officials],
        "spans": [
            {
                "handle": span.handle,
                "kind": span.kind,
                "label": span.label,
                "cite": span.cite(),
                "text": span.text,
                "sha256": span.sha256,
                "start_ms": span.start_ms,
                "end_ms": span.end_ms,
                "page": span.page_number,
                "result": span.result or None,
                "source_kind": span.source_kind or None,
                "tally": dict(span.tally) if span.tally else None,
            }
            for span in pack
        ],
    }


def check(pack: EvidencePack, draft: str) -> dict[str, Any]:
    """Check every sentence of a draft against the pack. Nothing is rewritten here."""
    verdicts = check_answer(draft, pack)
    return {
        "sentences": len(verdicts),
        "grounded": sum(1 for v in verdicts if v.ok),
        "verdicts": [
            {"sentence": v.sentence, "ok": v.ok, "code": v.code, "reason": v.reason}
            for v in verdicts
        ],
    }
