"""Stage a meeting for review, and assemble an approved item into a site episode."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from civiccast_brief.article.title import check_title, load_title, render_issues
from civiccast_brief.article.writer import (
    ArticleFormatError,
    caption_windows,
    check_article,
    load_article,
    load_documents,
    site_article,
)
from civiccast_brief.audio.ffmpeg import probe_duration
from civiccast_brief.meeting import transcript as transcript_mod
from civiccast_brief.review.desk import stage_item
from civiccast_brief.script.checker import check_script
from civiccast_brief.script.recap import Recap, load_recap
from civiccast_brief.script.writer import ROUNDUP_ID, load_script, render_txt
from civiccast_brief.site.titles import roundup_title

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
    """The recap and the transcript. A roundup has no single transcript, so it gets none."""
    recap_path = meeting_dir / "recap.json"
    if not recap_path.exists():
        raise EpisodeError(f"missing {recap_path}")
    recap = load_recap(recap_path)
    if recap.video_id == ROUNDUP_ID:
        return recap, {}
    transcript_path = meeting_dir / "transcript.json"
    if not transcript_path.exists():
        raise EpisodeError(f"missing {transcript_path}")
    return recap, json.loads(transcript_path.read_text(encoding="utf-8"))


def _meeting_id(recap: Recap, data: dict[str, Any]) -> str:
    if recap.video_id == ROUNDUP_ID:
        return f"roundup {recap.meeting_date}"
    return str(data["video_id"])


def stage(db: Path, meeting_dir: Path, script_json: Path) -> int:
    """Add the meeting to the desk. Returns the new item id.

    The script is checked against the recap again. A script that passes is published at once;
    one that fails is held in review.
    """
    recap, data = _meeting(meeting_dir)
    script = load_script(script_json)
    report = check_script(recap, script)
    item = stage_item(
        db,
        kind="episode",
        meeting_id=_meeting_id(recap, data),
        title=str(data.get("title") or f"{recap.body} {recap.meeting_date}"),
        meeting_date=recap.meeting_date,
        body=recap.body,
        checks_passed=report.passed,
        checks_note=report.render(),
        speakers=voice_map(meeting_dir),
        recap=recap_text(recap),
        script=render_txt(script),
    )
    return item.id


def _same(label: str, exported: str, expected: str) -> None:
    if exported.replace("\r\n", "\n").strip() != expected.replace("\r\n", "\n").strip():
        raise EpisodeError(
            f"the approved {label} differs from the one that would be published; "
            "stage and publish the item again"
        )


def _iso_date(long_date: str) -> str:
    """ISO date of a recap date; for a roundup ("A and B"), the last meeting's date."""
    last = long_date.split(" and ")[-1].strip()
    return datetime.strptime(last, "%B %d, %Y").date().isoformat()


def _headline(recap: Recap, meeting_dir: Path, roundup: bool) -> str:
    """The written title from title.json when there is one and it passes, else the dated one."""
    path = meeting_dir / "title.json"
    if path.exists():
        title = load_title(path)
        issues = check_title(recap, title)
        if issues:
            raise EpisodeError(f"{path} fails the check: {render_issues(issues)}")
        return title.text
    if roundup:
        days = [
            datetime.strptime(d.strip(), "%B %d, %Y").date()
            for d in recap.meeting_date.split(" and ")
        ]
        return roundup_title(recap.city, days)
    return f"{recap.city} {recap.body}, {recap.meeting_date}"


def _article(
    article_dir: Path,
    recap: Recap,
    data: dict[str, Any],
    segments: list[Any],
    documents: Path | None = None,
) -> dict[str, Any]:
    """The published story for the episode, in the site's format, checked once more.

    ``documents`` is the meeting's ``documents.json``, for a story that cites document pages.
    """
    meta = json.loads((article_dir / "item.json").read_text(encoding="utf-8"))
    if meta.get("kind") != "article" or meta.get("status") != "published":
        raise EpisodeError(f"{article_dir} is not a published article export")
    if meta.get("meeting_id") != _meeting_id(recap, data):
        raise EpisodeError(
            f"{article_dir} is the story of meeting {meta.get('meeting_id')}, "
            f"not {_meeting_id(recap, data)}"
        )
    try:
        article = load_article(article_dir / "article.json")
        windows = caption_windows(recap, segments) if segments else None
        pages = load_documents(documents) if documents and documents.exists() else None
        report = check_article(recap, article, windows, pages)
        if not report.passed:
            raise EpisodeError(f"{article_dir} fails the check:\n{report.render()}")
        return site_article(recap, article, pages)
    except ArticleFormatError as exc:
        raise EpisodeError(f"{article_dir}: {exc}") from exc


def episode_slug(recap: Recap, city_slug: str) -> str:
    """The id of a meeting's episode, and of its story: "2026-11-03-longmont-city-council".

    Only lowercase letters, digits and hyphens, which is what the site accepts: "Longmont Urban
    Renewal Authority (LURA)" is "longmont-urban-renewal-authority-lura", and the city's name is
    not said twice ("2026-11-10-longmont-urban-renewal-authority-lura")."""
    if recap.video_id == ROUNDUP_ID:
        kind = "weekly-roundup"
    else:
        kind = re.sub(r"[^a-z0-9]+", "-", recap.body.lower()).strip("-")
        kind = kind.removeprefix(f"{city_slug}-") or kind
    return f"{_iso_date(recap.meeting_date)}-{city_slug}-{kind}"


def assemble_story(
    *,
    article_dir: Path,
    meeting_dir: Path,
    city_slug: str,
    published: str,
) -> dict[str, Any]:
    """A published story as a stand-alone site file: no audio, no script. The story is checked
    again against the recap, exactly as when it is added to an episode.

    ``published`` is used when the item has no publish time of its own.
    """
    recap, data = _meeting(meeting_dir)
    if recap.video_id == ROUNDUP_ID:
        raise EpisodeError("a roundup has no stand-alone story")
    segments = transcript_mod.from_json(data)
    article = _article(article_dir, recap, data, segments, meeting_dir / "documents.json")
    meta = json.loads((article_dir / "item.json").read_text(encoding="utf-8"))
    published_at = str(meta.get("published_at") or "")
    return {
        "id": episode_slug(recap, city_slug),
        "city": city_slug,
        "approved": True,
        "approved_by": f"editor desk, {published_at or published}",
        "board": recap.body,
        "meeting_title": str(data.get("title") or recap.body),
        "meeting_date": _iso_date(recap.meeting_date),
        "published": _aware(published_at) or published,
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
        "video_url": _video_url(data),
        "article": article,
    }


def _aware(text: str) -> str | None:
    """The text when it is an ISO time with a zone offset, else None."""
    try:
        return text if datetime.fromisoformat(text).tzinfo is not None else None
    except ValueError:
        return None


def assemble(
    *,
    item_dir: Path,
    meeting_dir: Path,
    script_json: Path,
    audio_dir: Path,
    media_base_url: str,
    city_slug: str,
    published: str,
    article_dir: Path | None = None,
) -> dict[str, Any]:
    """One site episode from an exported, approved item. Refuses if anything was changed."""
    meta = json.loads((item_dir / "item.json").read_text(encoding="utf-8"))
    # "approved"/"approved_at" are from exports made before the editor desk.
    published_at = meta.get("published_at") or meta.get("approved_at")
    if meta.get("status") not in ("published", "approved") or not published_at:
        raise EpisodeError(f"{item_dir} is not a published export")
    recap, data = _meeting(meeting_dir)
    script = load_script(script_json)
    _same("recap", (item_dir / "recap.txt").read_text(encoding="utf-8"), recap_text(recap))
    _same("script", (item_dir / "script.txt").read_text(encoding="utf-8"), render_txt(script))

    mp3 = audio_dir / "episode.mp3"
    chapters = json.loads((audio_dir / "chapters.json").read_text(encoding="utf-8"))
    roundup = recap.video_id == ROUNDUP_ID
    day = _iso_date(recap.meeting_date)
    slug = episode_slug(recap, city_slug)
    base = media_base_url.rstrip("/")
    names = meta.get("speakers") or {}
    segments = [] if roundup else transcript_mod.from_json(data)
    episode: dict[str, Any] = {
        "id": slug,
        "city": city_slug,
        "approved": True,
        "approved_by": f"editor desk, {published_at}",
        "board": recap.body,
        "meeting_title": "Weekly roundup" if roundup else str(data.get("title") or recap.body),
        "meeting_date": day,
        "published": published,
        "headline": _headline(recap, meeting_dir, roundup),
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
        "video_url": None if roundup else _video_url(data),
        "transcript": None
        if roundup
        else [
            {
                "t": transcript_mod.hms(s.start),
                "speaker": _speaker(s, names),
                "text": s.text,
            }
            for s in segments
            if s.text
        ],
    }
    if article_dir is not None:
        episode["article"] = _article(
            article_dir, recap, data, segments, meeting_dir / "documents.json"
        )
    return episode


def _video_url(data: dict[str, Any]) -> str:
    """The meeting video: the capture's own link, or YouTube for transcripts made before it."""
    source = data.get("source") or {}
    return str(source.get("video_url") or f"https://www.youtube.com/watch?v={data['video_id']}")


def _speaker(segment: transcript_mod.Segment, names: dict[str, str]) -> str:
    if segment.speaker and segment.speaker != UNKNOWN:
        return segment.speaker
    named = names.get(segment.voice or "", UNKNOWN)
    return named if named != UNKNOWN else UNKNOWN
