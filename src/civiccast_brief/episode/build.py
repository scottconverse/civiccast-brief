"""Stage a meeting for review, and assemble an approved item into a site episode."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from civiccast_brief.audio.ffmpeg import probe_duration
from civiccast_brief.meeting import transcript as transcript_mod
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.script.recap import Recap, load_recap
from civiccast_brief.script.writer import load_script, render_txt

UNKNOWN = "unknown"


class EpisodeError(ValueError):
    """The inputs do not fit together; the message says which file and why."""


def recap_text(recap: Recap) -> str:
    """The recap as the producer reads it: one claim per line, with its id and time."""
    lines = [f"{recap.city} {recap.body}, {recap.meeting_date}"]
    lines += [f"{c.id} [{c.timestamp}] {c.topic}: {c.text}" for c in recap.claims]
    return "\n".join(lines) + "\n"


def voice_map(meeting_dir: Path) -> dict[str, str]:
    """Voice labels for the producer to name. Every voice starts as "unknown"."""
    path = meeting_dir / "speakers.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    labels = (data.get("diarization") or {}).get("labels") or {}
    return {voice: UNKNOWN for voice in sorted(labels.values())}


def _meeting(meeting_dir: Path) -> tuple[Recap, dict[str, Any]]:
    recap_path = meeting_dir / "recap.json"
    transcript_path = meeting_dir / "transcript.json"
    for path in (recap_path, transcript_path):
        if not path.exists():
            raise EpisodeError(f"missing {path}")
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    return load_recap(recap_path), data


def stage(db: Path, meeting_dir: Path, script_json: Path) -> int:
    """Add the meeting as a draft review item. Returns the new item id."""
    recap, data = _meeting(meeting_dir)
    script = load_script(script_json)
    with ReviewStore(db) as store:
        item = store.add_item(
            meeting_id=str(data["video_id"]),
            title=str(data.get("title") or f"{recap.body} {recap.meeting_date}"),
            meeting_date=recap.meeting_date,
            speakers=voice_map(meeting_dir),
            recap=recap_text(recap),
            script=render_txt(script),
        )
    return item.id


def _same(label: str, exported: str, expected: str) -> None:
    if exported.replace("\r\n", "\n").strip() != expected.replace("\r\n", "\n").strip():
        raise EpisodeError(
            f"the approved {label} differs from the one that would be published; "
            "stage and approve the item again"
        )


def _iso_date(long_date: str) -> str:
    return datetime.strptime(long_date, "%B %d, %Y").date().isoformat()


def assemble(
    *,
    item_dir: Path,
    meeting_dir: Path,
    script_json: Path,
    audio_dir: Path,
    media_base_url: str,
    city_slug: str,
    published: str,
) -> dict[str, Any]:
    """One site episode from an exported, approved item. Refuses if anything was changed."""
    meta = json.loads((item_dir / "item.json").read_text(encoding="utf-8"))
    if meta.get("status") != "approved" or not meta.get("approved_at"):
        raise EpisodeError(f"{item_dir} is not an approved export")
    recap, data = _meeting(meeting_dir)
    script = load_script(script_json)
    _same("recap", (item_dir / "recap.txt").read_text(encoding="utf-8"), recap_text(recap))
    _same("script", (item_dir / "script.txt").read_text(encoding="utf-8"), render_txt(script))

    mp3 = audio_dir / "episode.mp3"
    chapters = json.loads((audio_dir / "chapters.json").read_text(encoding="utf-8"))
    video_id = str(data["video_id"])
    day = _iso_date(recap.meeting_date)
    slug = f"{day}-{city_slug}-{recap.body.lower().replace(' ', '-')}"
    base = media_base_url.rstrip("/")
    names = meta.get("speakers") or {}
    segments = transcript_mod.from_json(data)
    return {
        "id": slug,
        "city": city_slug,
        "approved": True,
        "approved_by": f"producer screen, {meta['approved_at']}",
        "board": recap.body,
        "meeting_title": str(data.get("title") or recap.body),
        "meeting_date": day,
        "published": published,
        "headline": f"{recap.city} {recap.body}, {recap.meeting_date}",
        "recap": {
            "claims": [
                {
                    "id": c.id,
                    "topic": c.topic,
                    "text": c.text,
                    "timestamp": c.timestamp,
                    "quote": c.quote,
                }
                for c in recap.claims
            ]
        },
        "script": render_txt([line for line in script if not line.is_chapter]),
        "audio": {
            "url": f"{base}/{slug}.mp3",
            "duration_s": round(probe_duration(mp3), 2),
            "bytes": mp3.stat().st_size,
            "type": "audio/mpeg",
        },
        "chapters": [
            {"start": ch["startTime"], "title": ch["title"]} for ch in chapters["chapters"]
        ],
        "chapters_url": f"{base}/{slug}-chapters.json",
        "vtt_url": f"{base}/{slug}.vtt",
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "transcript": [
            {
                "t": transcript_mod.hms(s.start),
                "speaker": _speaker(s, names),
                "text": s.text,
            }
            for s in segments
            if s.text
        ],
    }


def _speaker(segment: transcript_mod.Segment, names: dict[str, str]) -> str:
    if segment.speaker and segment.speaker != UNKNOWN:
        return segment.speaker
    named = names.get(segment.voice or "", UNKNOWN)
    return named if named != UNKNOWN else UNKNOWN
