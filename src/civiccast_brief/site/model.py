"""Input model: the city settings and the episode JSON files that one site build reads.

A build reads one directory. It holds ``city.json`` (the site and podcast settings), one JSON
file per episode and, in ``stories/``, one JSON file per published news story (a story reaches the
site with or without an episode). An episode holds the approved recap (claims with transcript timestamps), the
two-host script, the MP3 facts, chapters, and the links to the chapter file, the VTT transcript and
the source video. Only episodes with ``"approved": true`` reach the site. Every problem is collected
and reported together, so one bad file does not hide the others.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from civiccast_brief.site import directories
from civiccast_brief.site.timecode import parse_seconds
from civiccast_brief.site.titles import shorten_roundup_title

CITY_FILE = "city.json"
#: The city folder's roster (see civiccast_brief/city.py); not an episode.
ROSTER_FILE = "roster.json"
#: Folder (inside the episodes directory) of stories that have no episode yet.
STORIES_DIR = "stories"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
SCRIPT_LINE_RE = re.compile(r"^\[([A-Za-z0-9_ -]{1,24})\]\s*(\S.*)$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
DEFAULT_BOARD = "City Council"
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
    #: An Apple subcategory of ``category``, or "" for none.
    subcategory: str = ""


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
    signup_url: str | None
    about: tuple[str, ...]
    artwork_source: Path | None
    share_image_source: Path | None
    #: Show page links for Apple Podcasts and Spotify, set once the show is listed there.
    apple_podcasts_url: str | None = None
    spotify_url: str | None = None
    #: Show the "By email" band on the front page. Off by default; needs signup_url.
    email_signup: bool = False


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
class ArticleCite:
    #: Position of the cited claim in recap.claims, counting from 0.
    claim: int
    seconds: float


@dataclass(frozen=True)
class ArticlePage:
    """A page of a meeting document a paragraph cites ("packet" or "minutes")."""

    kind: str
    url: str
    page: int


@dataclass(frozen=True)
class ArticleParagraph:
    text: str
    cites: tuple[ArticleCite, ...]
    pages: tuple[ArticlePage, ...] = ()


@dataclass(frozen=True)
class ArticleSection:
    heading: str | None
    paragraphs: tuple[ArticleParagraph, ...]


@dataclass(frozen=True)
class ArticleVote:
    motion: str
    result: str
    tally: str | None
    seconds: float


@dataclass(frozen=True)
class ArticleQuote:
    text: str
    speaker: str
    seconds: float


@dataclass(frozen=True)
class Article:
    """A written news story for one meeting. Every paragraph cites recap claims."""

    headline: str
    dek: str
    sections: tuple[ArticleSection, ...]
    votes: tuple[ArticleVote, ...]
    quotes: tuple[ArticleQuote, ...]
    topics: tuple[str, ...]


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
    article: Article | None = None

    @property
    def meeting_name(self) -> str:
        """The meeting title without a trailing date that repeats the meeting day:
        "City Council Regular Session - 03 November 2026" is "City Council Regular Session"."""
        return _without_day(self.meeting_title, self.meeting_date)

    @property
    def display_headline(self) -> str:
        """The story headline (the article's when there is one), with a SAMPLE label on test renders."""
        headline = self.article.headline if self.article is not None else self.headline
        return f"SAMPLE: {headline}" if self.sample else headline

    @property
    def display_dek(self) -> str:
        """The article's dek when it has one, otherwise the episode dek."""
        if self.article is not None and self.article.dek:
            return self.article.dek
        return self.dek


@dataclass(frozen=True)
class Story:
    """A published news story for a meeting that has no published episode (no audio yet).

    It has its own page, appears in every list and in search, and has no player. Its id is the
    id the meeting's episode would get, so when the episode is published the story is merged
    into it and the story's address redirects to the episode page.
    """

    id: str
    city: str
    board: str
    meeting_title: str
    meeting_date: date
    published: datetime
    approved_by: str
    claims: tuple[Claim, ...]
    article: Article
    video_url: str | None
    sample: bool = False
    tags: tuple[str, ...] = ()

    @property
    def headline(self) -> str:
        return self.article.headline

    @property
    def dek(self) -> str:
        return self.article.dek

    @property
    def meeting_name(self) -> str:
        return _without_day(self.meeting_title, self.meeting_date)

    @property
    def display_headline(self) -> str:
        return f"SAMPLE: {self.headline}" if self.sample else self.headline

    @property
    def display_dek(self) -> str:
        return self.article.dek


@dataclass(frozen=True)
class SiteInput:
    city: City
    episodes: tuple[Episode, ...]
    """Approved episodes, newest first."""
    excluded: tuple[tuple[str, str], ...]
    """(episode id, reason) for each file that was valid but not approved."""
    stories: tuple[Story, ...] = ()
    """Approved stories with no episode of the same id, newest first."""
    merged_stories: tuple[str, ...] = ()
    """Ids of stories whose meeting has an episode: the story's address redirects to it."""


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

    def https_url(self, data: dict[str, Any], key: str) -> str | None:
        value = self.text(data, key, required=False, limit=2048)
        if not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            self.fail(key, "must be an https address, such as https://signup.example.org/subscribe")
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
    stories = _load_stories(directory / STORIES_DIR, city, problems)
    if problems:
        raise InputError(problems)
    approved.sort(key=lambda item: (item.published, item.meeting_date, item.id), reverse=True)
    approved, alone, merged = _merge_stories(approved, stories)
    return SiteInput(
        city=city,
        episodes=tuple(approved),
        excluded=tuple(excluded),
        stories=alone,
        merged_stories=merged,
    )


def _merge_stories(
    episodes: list[Episode], stories: list[Story]
) -> tuple[list[Episode], tuple[Story, ...], tuple[str, ...]]:
    """Give an episode the story of its meeting when it has none; keep the rest as stories."""
    episodes = list(episodes)
    position = {episode.id: index for index, episode in enumerate(episodes)}
    alone: list[Story] = []
    merged: list[str] = []
    for story in stories:
        index = position.get(story.id)
        if index is None:
            alone.append(story)
            continue
        merged.append(story.id)
        if episodes[index].article is None:
            episodes[index] = replace(episodes[index], article=story.article)
    alone.sort(key=lambda item: (item.published, item.meeting_date, item.id), reverse=True)
    return episodes, tuple(alone), tuple(sorted(merged))


def _load_stories(directory: Path, city: City, problems: list[str]) -> list[Story]:
    stories: list[Story] = []
    if not directory.is_dir():
        return stories
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        story = _load_story(path, problems)
        if story is None:
            continue
        if story.id in seen:
            problems.append(f"{STORIES_DIR}/{path.name}: id {story.id!r} is used twice")
            continue
        seen.add(story.id)
        if story.city != city.slug:
            problems.append(
                f"{STORIES_DIR}/{path.name}: city {story.city!r} does not match "
                f"city.json ({city.slug!r})"
            )
            continue
        stories.append(story)
    return stories


def _load_story(path: Path, problems: list[str]) -> Story | None:
    data = _read_object(path, problems)
    if data is None:
        return None
    before = len(problems)
    check = _Checker(f"{STORIES_DIR}/{path.name}", problems)
    story_id = check.slug(data, "id")
    city = check.slug(data, "city")
    approved_by = check.text(data, "approved_by", limit=120)
    board = check.text(data, "board", required=False, limit=120) or DEFAULT_BOARD
    meeting_title = check.text(data, "meeting_title", limit=200)
    meeting_date = check.day(data, "meeting_date")
    published = check.moment(data, "published")
    sample = check.boolean(data, "sample", False)
    claims = _claims(check, data)
    video_url = check.url(data, "video_url", required=False)
    if data.get("article") is None:
        check.fail("article", "is required in a story file")
        return None
    article = _article(check, data, len(claims))
    if len(problems) > before or article is None:
        return None
    assert meeting_date is not None and published is not None
    return Story(
        id=story_id,
        city=city,
        board=board,
        meeting_title=meeting_title,
        meeting_date=meeting_date,
        published=published,
        approved_by=approved_by,
        claims=claims,
        article=article,
        video_url=video_url,
        sample=sample,
    )


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
        subcategory=podcast_check.text(podcast_raw, "subcategory", required=False, limit=80),
    )
    _check_podcast(podcast_check, podcast)

    artwork = _local_image(check, data, "artwork", directory)
    if artwork is not None:
        for problem in directories.artwork_problems(artwork.read_bytes(), artwork.suffix):
            check.fail("artwork", problem)
    share = _local_image(check, data, "share_image", directory)
    about_raw = data.get("about", [])
    about: list[str] = []
    if not isinstance(about_raw, list) or not all(isinstance(p, str) for p in about_raw):
        check.fail("about", "must be a list of paragraphs")
    else:
        about = [p.strip() for p in about_raw if p.strip()]

    signup_url = check.https_url(data, "signup_url")
    email_signup = check.boolean(data, "email_signup", False)
    if email_signup and not signup_url:
        check.fail("email_signup", "needs signup_url, the address the sign-up form posts to")

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
        signup_url=signup_url,
        about=tuple(about),
        artwork_source=artwork,
        share_image_source=share,
        apple_podcasts_url=check.https_url(data, "apple_podcasts_url"),
        spotify_url=check.https_url(data, "spotify_url"),
        email_signup=email_signup,
    )


def _check_podcast(check: _Checker, podcast: PodcastInfo) -> None:
    """The podcast settings Apple Podcasts and Spotify check (see site/directories.py)."""
    problem = directories.category_problem(podcast.category, podcast.subcategory)
    if problem:
        check.fail(*problem)
    if not directories.LANGUAGE_RE.match(podcast.language):
        check.fail("language", 'must be an ISO 639 language code, such as "en" or "en-us"')
    if podcast.owner_email and not directories.EMAIL_RE.match(podcast.owner_email):
        check.fail(
            "owner_email",
            "must be an email address; Spotify sends its ownership code to it",
        )
    size = len((podcast.description or "").encode("utf-8"))
    if size > directories.SHOW_DESCRIPTION_MAX_BYTES:
        check.fail("description", f"is {size} bytes; Apple allows 4000")


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
    board = check.text(data, "board", required=False, limit=120) or DEFAULT_BOARD
    meeting_title = check.text(data, "meeting_title", limit=200)
    meeting_date = check.day(data, "meeting_date")
    published = check.moment(data, "published")
    headline = shorten_roundup_title(check.text(data, "headline", limit=200))
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
    article = _article(check, data, len(claims))

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
        article=article,
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
    if kind not in directories.AUDIO_TYPES:
        check.fail("audio.type", f"must be one of {', '.join(directories.AUDIO_TYPES)}")
    if url is None or duration is None or size is None:
        return None
    return Audio(url=url, duration_s=duration, bytes=int(size), type=kind)


#: What can sit between a meeting's name and the date after it: "Session - 03 November 2026",
#: "City Council on 2026-10-06 3:30 PM".
_DAY_SEPARATOR = re.compile(r"\s+(?:[-\u2013\u2014|:]|on)\s+|,\s+", re.IGNORECASE)
_CLOCK = re.compile(r"\s+\d{1,2}:\d{2}\s*[AP]M\s*$", re.IGNORECASE)
_DAY_FORMATS = ("%d %B %Y", "%B %d %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y")


def _is_day(text: str, day: date) -> bool:
    text = _CLOCK.sub("", text).strip()
    for form in _DAY_FORMATS:
        try:
            if datetime.strptime(text, form).date() == day:
                return True
        except ValueError:
            continue
    return False


def _without_day(title: str, day: date) -> str:
    for separator in _DAY_SEPARATOR.finditer(title):
        if separator.start() > 0 and _is_day(title[separator.end() :], day):
            return title[: separator.start()].rstrip()
    return title


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
            # Titles written before 2026-11-11 can start in lower case ("public comment").
            chapters.append(Chapter(start=start, title=title[0].upper() + title[1:]))
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


def _start(check: _Checker, item: dict[str, Any], label: str) -> float | None:
    value = item.get("start_s")
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        check.fail(f"{label}.start_s", "must be a number of seconds, 0 or more")
        return None
    return float(value)


def _list(check: _Checker, data: dict[str, Any], key: str) -> list[Any]:
    raw = data.get(key, [])
    if not isinstance(raw, list):
        check.fail(key, "must be a list")
        return []
    return raw


def _cites(
    check: _Checker, paragraph: dict[str, Any], label: str, claim_count: int
) -> tuple[ArticleCite, ...]:
    raw = paragraph.get("cites")
    if not isinstance(raw, list) or not raw:
        check.fail(f"{label}.cites", "must list at least one claim")
        return ()
    cites: list[ArticleCite] = []
    for index, cite in enumerate(raw, start=1):
        c_label = f"{label}.cites[{index}]"
        if not isinstance(cite, dict):
            check.fail(c_label, "must be an object")
            continue
        claim = cite.get("claim")
        if isinstance(claim, bool) or not isinstance(claim, int):
            check.fail(f"{c_label}.claim", "must be a whole number")
            continue
        if not 0 <= claim < claim_count:
            check.fail(
                f"{c_label}.claim",
                f"is {claim}, but the recap has claims 0 to {claim_count - 1}"
                if claim_count
                else f"is {claim}, but the recap has no claims",
            )
            continue
        start = _start(check, cite, c_label)
        if start is not None:
            cites.append(ArticleCite(claim=claim, seconds=start))
    return tuple(cites)


def _pages(check: _Checker, paragraph: dict[str, Any], label: str) -> tuple[ArticlePage, ...]:
    """The optional document pages of a paragraph: an http(s) document and a page from 1."""
    pages: list[ArticlePage] = []
    for index, page in enumerate(_list(check, paragraph, "pages"), start=1):
        p_label = f"{label}.pages[{index}]"
        if not isinstance(page, dict):
            check.fail(p_label, "must be an object")
            continue
        kind = check.text(page, "kind", limit=40)
        url = check.https_url(page, "url")
        if not url:
            check.fail(f"{p_label}.url", "must be the https address of the document")
            continue
        number = page.get("page")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            check.fail(f"{p_label}.page", "must be a page number from 1")
            continue
        pages.append(ArticlePage(kind=kind, url=url, page=number))
    return tuple(pages)


def _sections(check: _Checker, raw: dict[str, Any], claim_count: int) -> tuple[ArticleSection, ...]:
    items = raw.get("sections")
    if not isinstance(items, list) or not items:
        check.fail("sections", "must be a list with at least one section")
        return ()
    sections: list[ArticleSection] = []
    for s_index, section in enumerate(items, start=1):
        label = f"sections[{s_index}]"
        if not isinstance(section, dict):
            check.fail(label, "must be an object")
            continue
        heading = section.get("heading")
        if heading is not None and (not isinstance(heading, str) or not heading.strip()):
            check.fail(f"{label}.heading", "must be text or null")
            heading = None
        raw_paragraphs = section.get("paragraphs")
        if not isinstance(raw_paragraphs, list) or not raw_paragraphs:
            check.fail(f"{label}.paragraphs", "must be a list with at least one paragraph")
            continue
        paragraphs: list[ArticleParagraph] = []
        for p_index, paragraph in enumerate(raw_paragraphs, start=1):
            p_label = f"{label}.paragraphs[{p_index}]"
            if not isinstance(paragraph, dict):
                check.fail(p_label, "must be an object")
                continue
            text = check.text(paragraph, "text", limit=4000)
            cites = _cites(check, paragraph, p_label, claim_count)
            pages = _pages(check, paragraph, p_label)
            paragraphs.append(ArticleParagraph(text=text, cites=cites, pages=pages))
        sections.append(
            ArticleSection(
                heading=heading.strip()[:200] if isinstance(heading, str) else None,
                paragraphs=tuple(paragraphs),
            )
        )
    return tuple(sections)


def _votes(check: _Checker, raw: dict[str, Any]) -> tuple[ArticleVote, ...]:
    votes: list[ArticleVote] = []
    for index, vote in enumerate(_list(check, raw, "votes"), start=1):
        label = f"votes[{index}]"
        if not isinstance(vote, dict):
            check.fail(label, "must be an object")
            continue
        motion = check.text(vote, "motion", limit=300)
        result = check.text(vote, "result", limit=80)
        tally = vote.get("tally")
        if tally is not None and (not isinstance(tally, str) or len(tally) > 40):
            check.fail(f"{label}.tally", "must be short text such as 5-2, or null")
            tally = None
        start = _start(check, vote, label)
        if motion and result and start is not None:
            votes.append(
                ArticleVote(
                    motion=motion,
                    result=result,
                    tally=(tally.strip() or None) if isinstance(tally, str) else None,
                    seconds=start,
                )
            )
    return tuple(votes)


def _quotes(check: _Checker, raw: dict[str, Any]) -> tuple[ArticleQuote, ...]:
    quotes: list[ArticleQuote] = []
    for index, quote in enumerate(_list(check, raw, "quotes"), start=1):
        label = f"quotes[{index}]"
        if not isinstance(quote, dict):
            check.fail(label, "must be an object")
            continue
        text = check.text(quote, "text", limit=1000)
        speaker = check.text(quote, "speaker", limit=120)
        start = _start(check, quote, label)
        if text and speaker and start is not None:
            quotes.append(ArticleQuote(text=text, speaker=speaker, seconds=start))
    return tuple(quotes)


def _article(check: _Checker, data: dict[str, Any], claim_count: int) -> Article | None:
    """The optional written story. Every paragraph must cite claims that are in recap.claims."""
    if data.get("article") is None:
        return None
    raw = data["article"]
    if not isinstance(raw, dict):
        check.fail("article", "must be an object")
        return None
    sub = _Checker(f"{check.where} article", check.problems)
    before = len(check.problems)
    headline = sub.text(raw, "headline", limit=200)
    dek = sub.text(raw, "dek", required=False, limit=400)
    sections = _sections(sub, raw, claim_count)
    votes = _votes(sub, raw)
    quotes = _quotes(sub, raw)
    topics_raw = _list(sub, raw, "topics")
    if not all(isinstance(t, str) for t in topics_raw):
        sub.fail("topics", "must be a list of words")
        topics_raw = []
    if len(check.problems) > before:
        return None
    return Article(
        headline=headline,
        dek=dek,
        sections=sections,
        votes=votes,
        quotes=quotes,
        topics=tuple(t.strip() for t in topics_raw if t.strip())[:12],
    )


__all__ = [
    "Article",
    "ArticleCite",
    "ArticlePage",
    "ArticleParagraph",
    "ArticleQuote",
    "ArticleSection",
    "ArticleVote",
    "Audio",
    "Chapter",
    "City",
    "Claim",
    "Episode",
    "InputError",
    "PodcastInfo",
    "ScriptLine",
    "SiteInput",
    "Story",
    "TranscriptLine",
    "load_site_input",
]
