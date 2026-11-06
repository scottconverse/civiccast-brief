"""Input model: the city settings and the episode JSON files that one site build reads.

A build reads one directory. It holds ``city.json`` (the site and podcast settings) and one JSON
file per episode. An episode holds the approved recap (claims with transcript timestamps), the
two-host script, the MP3 facts, chapters, and the links to the chapter file, the VTT transcript and
the source video. Only episodes with ``"approved": true`` reach the site. Every problem is collected
and reported together, so one bad file does not hide the others.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from civiccast_brief.site.timecode import parse_seconds

CITY_FILE = "city.json"
#: The city folder's roster (see civiccast_brief/city.py); not an episode.
ROSTER_FILE = "roster.json"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
SCRIPT_LINE_RE = re.compile(r"^\[([A-Za-z0-9_ -]{1,24})\]\s*(\S.*)$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
DEFAULT_DISCLAIMER = (
    "This episode is AI-generated from the public meeting video. Check the official record "
    "before you rely on anything it says."
)


class InputError(ValueError):
    """The input directory has one or more problems. ``problems`` lists each one."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class PodcastInfo:
    name: str
    author: str
    owner_name: str
    owner_email: str
    description: str
    category: str
    language: str
    explicit: bool


@dataclass(frozen=True)
class City:
    slug: str
    name: str
    site_title: str
    tagline: str
    publisher: str
    disclaimer: str
    hosts: dict[str, str]
    podcast: PodcastInfo
    official_site: str | None
    contact_email: str | None
    about: tuple[str, ...]
    artwork_source: Path | None
    share_image_source: Path | None


@dataclass(frozen=True)
class Claim:
    id: str
    topic: str
    text: str
    seconds: float
    quote: str


@dataclass(frozen=True)
class ScriptLine:
    speaker: str
    text: str


@dataclass(frozen=True)
class TranscriptLine:
    seconds: float
    speaker: str
    text: str


@dataclass(frozen=True)
class Chapter:
    start: float
    title: str


@dataclass(frozen=True)
class Audio:
    url: str
    duration_s: float
    bytes: int
    type: str


@dataclass(frozen=True)
class Correction:
    date: date
    note: str


@dataclass(frozen=True)
class Episode:
    id: str
    city: str
    board: str
    meeting_title: str
    meeting_date: date
    published: datetime
    headline: str
    dek: str
    tags: tuple[str, ...]
    sample: bool
    approved_by: str
    claims: tuple[Claim, ...]
    script: tuple[ScriptLine, ...]
    audio: Audio
    chapters: tuple[Chapter, ...]
    chapters_url: str | None
    vtt_url: str | None
    video_url: str | None
    transcript: tuple[TranscriptLine, ...] | None
    corrections: tuple[Correction, ...]

    @property
    def display_headline(self) -> str:
        """The headline with a SAMPLE label on test renders."""
        return f"SAMPLE: {self.headline}" if self.sample else self.headline


@dataclass(frozen=True)
class SiteInput:
    city: City
    episodes: tuple[Episode, ...]
    """Approved episodes, newest first."""
    excluded: tuple[tuple[str, str], ...]
    """(episode id, reason) for each file that was valid but not approved."""


class _Checker:
    """Collects problems for one file while reading its fields."""

    def __init__(self, where: str, problems: list[str]) -> None:
        self.where = where
        self.problems = problems

    def fail(self, key: str, message: str) -> None:
        self.problems.append(f"{self.where}: {key} {message}")

    def text(
        self, data: dict[str, Any], key: str, *, required: bool = True, limit: int = 2000
    ) -> str:
        value = data.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                self.fail(key, "is required")
            return ""
        if not isinstance(value, str):
            self.fail(key, "must be text")
            return ""
        value = value.strip()
        if len(value) > limit:
            self.fail(key, f"is longer than {limit} characters")
        if CONTROL_RE.search(value):
            self.fail(key, "contains control characters")
        return value

    def slug(self, data: dict[str, Any], key: str) -> str:
        value = self.text(data, key, limit=80)
        if value and not SLUG_RE.match(value):
            self.fail(key, "must be lowercase letters, digits and hyphens")
        return value

    def boolean(self, data: dict[str, Any], key: str, default: bool) -> bool:
        value = data.get(key, default)
        if not isinstance(value, bool):
            self.fail(key, "must be true or false")
            return default
        return value

    def url(self, data: dict[str, Any], key: str, *, required: bool) -> str | None:
        value = self.text(data, key, required=required, limit=2048)
        if not value:
            return None
        if value.startswith("/") and not value.startswith("//"):
            return value
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            self.fail(key, "must be an http or https address, or a path starting with /")
            return None
        return value

    def day(self, data: dict[str, Any], key: str) -> date | None:
        value = self.text(data, key, limit=10)
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.fail(key, "must be a date such as 2026-10-20")
            return None

    def moment(self, data: dict[str, Any], key: str) -> datetime | None:
        value = self.text(data, key, limit=40)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            self.fail(
                key, "must be a date and time with a timezone, such as 2026-10-21T09:00:00-06:00"
            )
            return None
        if parsed.tzinfo is None:
            self.fail(key, "must include a timezone offset")
            return None
        return parsed

    def seconds(self, data: dict[str, Any], key: str, *, where_label: str = "") -> float | None:
        if key not in data or data[key] is None:
            self.fail(key, "is required")
            return None
        try:
            return parse_seconds(data[key])
        except ValueError as exc:
            self.fail(where_label or key, str(exc))
            return None

    def number(self, data: dict[str, Any], key: str, *, positive: bool = True) -> float | None:
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            self.fail(key, "must be a number")
            return None
        if positive and value <= 0:
            self.fail(key, "must be greater than zero")
            return None
        return float(value)


def load_site_input(directory: Path) -> SiteInput:
    """Read ``city.json`` and every other ``*.json`` file in ``directory``.

    Raises InputError listing every problem. Episodes that are valid but not approved are returned
    in ``excluded`` and are not in ``episodes``.
    """
    if not directory.is_dir():
        raise InputError([f"{directory}: not a directory"])
    problems: list[str] = []
    city_path = directory / CITY_FILE
    if not city_path.is_file():
        raise InputError([f"{city_path}: city.json is required in the episodes directory"])
    city = _load_city(city_path, directory, problems)

    approved: list[Episode] = []
    excluded: list[tuple[str, str]] = []
    seen_ids: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name in (CITY_FILE, ROSTER_FILE):
            continue
        episode = _load_episode(path, problems)
        if episode is None:
            continue
        if episode.id in seen_ids:
            problems.append(
                f"{path.name}: id {episode.id!r} is also used by {seen_ids[episode.id]}"
            )
            continue
        seen_ids[episode.id] = path.name
        if episode.city != city.slug:
            problems.append(
                f"{path.name}: city {episode.city!r} does not match city.json ({city.slug!r})"
            )
            continue
        if episode.approved_by:
            approved.append(episode)
        else:
            excluded.append((episode.id, "not approved"))

    if problems:
        raise InputError(problems)
    approved.sort(key=lambda item: item.published, reverse=True)
    return SiteInput(city=city, episodes=tuple(approved), excluded=tuple(excluded))


def _read_object(path: Path, problems: list[str]) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{path}: cannot read ({exc})")
        return None
    except json.JSONDecodeError as exc:
        problems.append(f"{path}: not valid JSON ({exc})")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path}: the top level must be a JSON object")
        return None
    return data


def _load_city(path: Path, directory: Path, problems: list[str]) -> City:
    data = _read_object(path, problems) or {}
    check = _Checker(CITY_FILE, problems)
    slug = check.slug(data, "slug")
    name = check.text(data, "name", limit=120)
    site_title = check.text(data, "site_title", required=False, limit=160) or (
        f"CivicCast Brief: {name}" if name else ""
    )
    hosts_raw = data.get("hosts", {"S1": "Host 1", "S2": "Host 2"})
    hosts: dict[str, str] = {}
    if not isinstance(hosts_raw, dict) or not hosts_raw:
        check.fail("hosts", "must map speaker tags such as S1 to names")
    else:
        for tag, host_name in hosts_raw.items():
            if not isinstance(host_name, str) or not host_name.strip():
                check.fail(f"hosts.{tag}", "must be a name")
            else:
                hosts[str(tag)] = host_name.strip()[:80]

    podcast_raw = data.get("podcast")
    if not isinstance(podcast_raw, dict):
        check.fail("podcast", "is required and must be an object")
        podcast_raw = {}
    podcast_check = _Checker(f"{CITY_FILE} podcast", problems)
    podcast = PodcastInfo(
        name=podcast_check.text(podcast_raw, "name", limit=160) or site_title,
        author=podcast_check.text(podcast_raw, "author", limit=120) or "CivicCast Brief",
        owner_name=podcast_check.text(podcast_raw, "owner_name", limit=120) or "CivicCast Brief",
        owner_email=podcast_check.text(podcast_raw, "owner_email", limit=200),
        description=podcast_check.text(podcast_raw, "description", limit=1000),
        category=podcast_check.text(podcast_raw, "category", required=False, limit=80)
        or "Government",
        language=podcast_check.text(podcast_raw, "language", required=False, limit=20) or "en-us",
        explicit=podcast_check.boolean(podcast_raw, "explicit", False),
    )

    artwork = _local_image(check, data, "artwork", directory)
    share = _local_image(check, data, "share_image", directory)
    about_raw = data.get("about", [])
    about: list[str] = []
    if not isinstance(about_raw, list) or not all(isinstance(p, str) for p in about_raw):
        check.fail("about", "must be a list of paragraphs")
    else:
        about = [p.strip() for p in about_raw if p.strip()]

    return City(
        slug=slug,
        name=name,
        site_title=site_title,
        tagline=check.text(data, "tagline", required=False, limit=300)
        or "What did our local government just do?",
        publisher=check.text(data, "publisher", required=False, limit=120) or "CivicCast Brief",
        disclaimer=check.text(data, "disclaimer", required=False, limit=600) or DEFAULT_DISCLAIMER,
        hosts=hosts or {"S1": "Host 1", "S2": "Host 2"},
        podcast=podcast,
        official_site=check.url(data, "official_site", required=False),
        contact_email=check.text(data, "contact_email", required=False, limit=200) or None,
        about=tuple(about),
        artwork_source=artwork,
        share_image_source=share,
    )


def _local_image(check: _Checker, data: dict[str, Any], key: str, directory: Path) -> Path | None:
    value = check.text(data, key, required=False, limit=260)
    if not value:
        return None
    path = (directory / value).resolve()
    if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        check.fail(key, f"must point to a PNG or JPEG file inside the episodes directory: {value}")
        return None
    try:
        path.relative_to(directory.resolve())
    except ValueError:
        check.fail(key, "must stay inside the episodes directory")
        return None
    return path


def _load_episode(path: Path, problems: list[str]) -> Episode | None:
    data = _read_object(path, problems)
    if data is None:
        return None
    before = len(problems)
    check = _Checker(path.name, problems)

    episode_id = check.slug(data, "id")
    city = check.slug(data, "city")
    approved = check.boolean(data, "approved", False)
    approved_by = check.text(data, "approved_by", required=False, limit=120)
    if approved and not approved_by:
        check.fail("approved_by", "is required when approved is true")
    if not approved:
        approved_by = ""
    board = check.text(data, "board", limit=120)
    meeting_title = check.text(data, "meeting_title", limit=200)
    meeting_date = check.day(data, "meeting_date")
    published = check.moment(data, "published")
    headline = check.text(data, "headline", limit=200)
    dek = check.text(data, "dek", required=False, limit=400)
    tags = _tags(check, data)
    sample = check.boolean(data, "sample", False)
    claims = _claims(check, data)
    script = _script(check, data)
    audio = _audio(check, data)
    chapters = _chapters(check, data)
    chapters_url = check.url(data, "chapters_url", required=False)
    vtt_url = check.url(data, "vtt_url", required=False)
    video_url = check.url(data, "video_url", required=False)
    transcript = _transcript(check, data)
    corrections = _corrections(check, data)

    if len(problems) > before:
        return None
    assert meeting_date is not None and published is not None and audio is not None
    assert script is not None
    return Episode(
        id=episode_id,
        city=city,
        board=board,
        meeting_title=meeting_title,
        meeting_date=meeting_date,
        published=published,
        headline=headline,
        dek=dek,
        tags=tags,
        sample=sample,
        approved_by=approved_by,
        claims=claims,
        script=script,
        audio=audio,
        chapters=chapters,
        chapters_url=chapters_url,
        vtt_url=vtt_url,
        video_url=video_url,
        transcript=transcript,
        corrections=corrections,
    )


def _tags(check: _Checker, data: dict[str, Any]) -> tuple[str, ...]:
    raw = data.get("tags", [])
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        check.fail("tags", "must be a list of words")
        return ()
    return tuple(t.strip() for t in raw if t.strip())[:12]


def _claims(check: _Checker, data: dict[str, Any]) -> tuple[Claim, ...]:
    recap = data.get("recap", {"claims": []})
    if not isinstance(recap, dict):
        check.fail("recap", "must be an object with a claims list")
        return ()
    raw = recap.get("claims", [])
    if not isinstance(raw, list):
        check.fail("recap.claims", "must be a list")
        return ()
    claims: list[Claim] = []
    ids: set[str] = set()
    for index, item in enumerate(raw, start=1):
        label = f"recap.claims[{index}]"
        if not isinstance(item, dict):
            check.fail(label, "must be an object")
            continue
        claim_id = check.text(item, "id", limit=40)
        if claim_id in ids:
            check.fail(label, f"id {claim_id!r} is used twice")
        ids.add(claim_id)
        seconds = check.seconds(item, "timestamp", where_label=f"{label}.timestamp")
        claim = Claim(
            id=claim_id,
            topic=check.text(item, "topic", required=False, limit=120),
            text=check.text(item, "text", limit=1000),
            seconds=seconds if seconds is not None else 0.0,
            quote=check.text(item, "quote", required=False, limit=2000),
        )
        if seconds is not None:
            claims.append(claim)
    return tuple(claims)


def _script(check: _Checker, data: dict[str, Any]) -> tuple[ScriptLine, ...] | None:
    text = check.text(data, "script", limit=40_000)
    if not text:
        return None
    lines: list[ScriptLine] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        match = SCRIPT_LINE_RE.match(raw.strip())
        if not match:
            check.fail("script", f"line {number} must start with a speaker tag such as [S1]")
            continue
        lines.append(ScriptLine(speaker=match.group(1), text=match.group(2).strip()))
    if not lines:
        check.fail("script", "has no spoken lines")
        return None
    return tuple(lines)


def _audio(check: _Checker, data: dict[str, Any]) -> Audio | None:
    raw = data.get("audio")
    if not isinstance(raw, dict):
        check.fail("audio", "is required and must be an object")
        return None
    url = check.url(raw, "url", required=True)
    duration = check.number(raw, "duration_s")
    size = check.number(raw, "bytes")
    kind = check.text(raw, "type", required=False, limit=40) or "audio/mpeg"
    if url is None or duration is None or size is None:
        return None
    return Audio(url=url, duration_s=duration, bytes=int(size), type=kind)


def _chapters(check: _Checker, data: dict[str, Any]) -> tuple[Chapter, ...]:
    raw = data.get("chapters", [])
    if not isinstance(raw, list):
        check.fail("chapters", "must be a list")
        return ()
    chapters: list[Chapter] = []
    for index, item in enumerate(raw, start=1):
        label = f"chapters[{index}]"
        if not isinstance(item, dict):
            check.fail(label, "must be an object")
            continue
        start = check.seconds(item, "start", where_label=f"{label}.start")
        title = check.text(item, "title", limit=160)
        if start is not None and title:
            chapters.append(Chapter(start=start, title=title))
    return tuple(chapters)


def _transcript(check: _Checker, data: dict[str, Any]) -> tuple[TranscriptLine, ...] | None:
    if "transcript" not in data or data["transcript"] is None:
        return None
    raw = data["transcript"]
    if not isinstance(raw, list):
        check.fail("transcript", "must be a list of lines")
        return None
    lines: list[TranscriptLine] = []
    for index, item in enumerate(raw, start=1):
        label = f"transcript[{index}]"
        if not isinstance(item, dict):
            check.fail(label, "must be an object")
            continue
        seconds = check.seconds(item, "t", where_label=f"{label}.t")
        speaker = check.text(item, "speaker", required=False, limit=80) or "unknown"
        text = check.text(item, "text", limit=1000)
        if seconds is not None and text:
            lines.append(TranscriptLine(seconds=seconds, speaker=speaker, text=text))
    return tuple(lines)


def _corrections(check: _Checker, data: dict[str, Any]) -> tuple[Correction, ...]:
    raw = data.get("corrections", [])
    if not isinstance(raw, list):
        check.fail("corrections", "must be a list")
        return ()
    items: list[Correction] = []
    for index, item in enumerate(raw, start=1):
        label = f"corrections[{index}]"
        if not isinstance(item, dict):
            check.fail(label, "must be an object")
            continue
        day = check.day(item, "date")
        note = check.text(item, "note", limit=1000)
        if day is not None and note:
            items.append(Correction(date=day, note=note))
    return tuple(sorted(items, key=lambda c: c.date, reverse=True))


__all__ = [
    "Audio",
    "Chapter",
    "City",
    "Claim",
    "Correction",
    "Episode",
    "InputError",
    "PodcastInfo",
    "ScriptLine",
    "SiteInput",
    "TranscriptLine",
    "load_site_input",
]
