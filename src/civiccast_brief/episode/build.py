"""Stage a meeting for review, and assemble an approved item into a site episode."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from civiccast_brief.article.names import (
    People,
    fixes,
    load_people,
    ordinary_words,
    seat_name_fixes,
)
from civiccast_brief.article.title import check_title, load_title, render_issues
from civiccast_brief.article.writer import (
    ArticleFormatError,
    caption_windows,
    check_article,
    claim_speakers,
    load_article,
    load_documents,
    merge_announcements,
    site_article,
)
from civiccast_brief.audio.ffmpeg import probe_duration
from civiccast_brief.meeting import transcript as transcript_mod
from civiccast_brief.meeting.roster import load_roster
from civiccast_brief.review.desk import stage_item
from civiccast_brief.script.checker import check_script
from civiccast_brief.script.recap import (
    Recap,
    channel_place,
    load_recap,
    named_bodies,
    named_place,
)
from civiccast_brief.script.writer import ROUNDUP_ID, load_script, parse_txt, render_txt
from civiccast_brief.site.titles import roundup_title

UNKNOWN = "unknown"


class EpisodeError(ValueError):
    """The inputs do not fit together; the message says which file and why."""


def recap_text(recap: Recap) -> str:
    """The recap as the producer reads it: one claim per line, with its id and time."""
    lines = [f"{named_place(recap)}, {recap.meeting_date}"]
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
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    # A meeting outside YouTube (CivicClerk, eScribe, Vimeo) has its own media address. Saving it
    # on the recap makes every cite a real time link instead of a youtube.com address that 404s.
    media = _media_url(data)
    if media:
        recap = replace(recap, media_url=media)
    return recap, data


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
    passed, note = report.passed, report.render()
    # Boulder, Nov 15 2026: a recap with no facts gave a 24-second intro and sign-off, and it
    # published by itself. An episode that tells nothing about the meeting waits for the editor.
    facts = sum(1 for line in script if not line.is_chapter and line.claim_ids)
    if not facts:
        passed = False
        note = (
            f"HELD: the script states no fact from the meeting "
            f"({len(recap.claims)} claim(s) in the recap).\n{note}"
        )
    item = stage_item(
        db,
        kind="episode",
        meeting_id=_meeting_id(recap, data),
        title=str(data.get("title") or f"{named_bodies(recap)} {recap.meeting_date}"),
        meeting_date=recap.meeting_date,
        body=named_bodies(recap),
        checks_passed=passed,
        checks_note=note,
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
        # A written title is a headline; the model sometimes starts one lowercase.
        return title.text[:1].upper() + title.text[1:]
    if roundup:
        days = [
            datetime.strptime(d.strip(), "%B %d, %Y").date()
            for d in recap.meeting_date.split(" and ")
        ]
        return roundup_title(recap.city, days)
    # "Longmont Housing Authority Board" already names the city; so does a body that names
    # the place its channel names ("Boulder Valley").
    place = channel_place(recap, meeting_dir)
    named = recap.body.lower().startswith(f"{recap.city.lower()} ") or (
        place and recap.body.lower().startswith(f"{place.lower()} ")
    )
    body = recap.body if named else f"{place or recap.city} {recap.body}"
    return f"{body}, {recap.meeting_date}"


def _story_recap(meeting_dir: Path, recap: Recap) -> tuple[Recap, bool]:
    """The recap the story's own CLAIMS AND SOURCES adds, when a skill run wrote one."""
    path = meeting_dir / "story-recap.json"
    if path.is_file():
        try:
            return load_recap(path), True
        except (OSError, ValueError):
            pass
    return recap, False


def _article(
    article_dir: Path,
    recap: Recap,
    data: dict[str, Any],
    segments: list[Any],
    documents: Path | None = None,
    evidence: Recap | None = None,
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
        article, _ = merge_announcements(article)
        found_roster = _roster_of(documents.parent if documents else None)
        roster = found_roster[0] if found_roster else None
        speakers = claim_speakers(recap, segments, roster) if segments and roster else None
        windows = caption_windows(recap, segments) if segments else None
        pages = load_documents(documents) if documents and documents.exists() else None
        people = _people_of(documents.parent if documents else None, documents)
        if evidence is None:
            evidence = recap
        skill_evidence = evidence is not recap
        report = check_article(
            evidence, article, windows, pages, speakers, people, skill_evidence=skill_evidence
        )
        if not report.passed:
            raise EpisodeError(f"{article_dir} fails the check:\n{report.render()}")
        return site_article(evidence, article, pages, people)
    except ArticleFormatError as exc:
        raise EpisodeError(f"{article_dir}: {exc}") from exc


def _roster_of(meeting_dir: Path | None) -> tuple[Any, str] | None:
    """The roster the run used, from speakers.json, so the story's names check the same way."""
    if meeting_dir is None:
        return None
    path = meeting_dir / "speakers.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    used = data.get("roster") or {}
    file = used.get("file")
    if not file:
        return None
    # The path was written by the run that made speakers.json; a relative one is resolved from
    # that run's work folder, which is an ancestor of the meeting folder.
    candidates = [Path(file)]
    candidates += [parent / file for parent in [meeting_dir, *meeting_dir.parents]]
    found = next((p for p in candidates if p.is_file()), None)
    if found is None:
        # Runs before Nov 6, 2026 wrote "default" for the built-in roster they used. The run
        # still recorded which city and body that roster was, so the city folder's roster of
        # that same body is the record for it.
        return _roster_of_body(
            meeting_dir, str(used.get("city") or ""), str(used.get("body") or "")
        )
    try:
        return load_roster(found), str(found)
    except (OSError, ValueError):
        return None


def _roster_of_body(meeting_dir: Path, city: str, body: str) -> tuple[Any, str] | None:
    """The one roster file in the nearest cities/ folder above the meeting for ``city``'s
    ``body``, or None when there is none or more than one."""
    if not city or not body:
        return None
    for parent in meeting_dir.parents:
        cities = parent / "cities"
        if not cities.is_dir():
            continue
        found = []
        for file in [*cities.glob("*/roster.json"), *cities.glob("*/rosters/*.json")]:
            try:
                roster = load_roster(file)
            except (OSError, ValueError):
                continue
            if (roster.city.casefold(), roster.body.casefold()) == (
                city.casefold(),
                body.casefold(),
            ):
                found.append((roster, str(file)))
        return found[0] if len(found) == 1 else None
    return None


def _people_of(meeting_dir: Path | None, documents: Path | None, roundup: bool = False) -> People:
    """The people the run's rosters and this meeting's documents name.

    A roundup covers several meetings, so its records are every meeting's documents beside it.
    """
    found = _roster_of(meeting_dir)
    roster, roster_path = found if found else (None, "")
    rosters = [(roster, roster_path)] if roster else []
    # The writer also saw the city's other body rosters (rosters/*.json). Their members are
    # matched only by exact name: see load_people.
    others = []
    for other in sorted((Path(roster_path).parent / "rosters").glob("*.json")):
        try:
            others.append((load_roster(other), str(other)))
        except (OSError, ValueError):
            continue
    pages = load_documents(documents) if documents and documents.exists() else None
    if roundup and meeting_dir is not None:
        merged = dict(pages or {})
        for sibling in sorted(meeting_dir.parent.glob("*/documents.json")):
            try:
                merged.update(load_documents(sibling))
            except (OSError, ValueError):
                continue
        pages = merged or None
    return load_people(rosters, pages, str(documents) if documents else "", others)


def _name_fixes(
    recap: Recap, people: People, transcript_texts: list[str] | tuple[str, ...] = ()
) -> list[dict[str, str]]:
    """The speech-to-text -> record name pairs for the recap and transcript the site prints.

    A name the site prints in the recap, a quote or a transcript page is set to the record's
    spelling. The correction is the names.py rule: a surname that matches the record (by sound
    or spelling, and by the printed seat or title) is certain; a phrase the record does not hold
    is left as the transcript has it, because guessing at every capitalised phrase in free text
    would rewrite the record wrongly.
    """
    pairs: list[tuple[str, str]] = []
    for claim in recap.claims:
        for text in (claim.text, claim.quote):
            pairs += seat_name_fixes(text, people)
            pairs += fixes(text, people)
    for text in transcript_texts:
        pairs += seat_name_fixes(text, people)
        pairs += fixes(text, people)
    # The site replaces a printed name wherever it appears, so a one-word name that this
    # meeting also says as an ordinary word ("Well" for Trustee Lowell, beside "well") is left
    # as printed.
    texts = [*(t for c in recap.claims for t in (c.text, c.quote)), *transcript_texts]
    ordinary = ordinary_words(texts)
    # A record name prints without its packet note: "Meosha Babbs (SVVSD)" is Meosha Babbs.
    pairs = [(wrong, re.sub(r"\s*\([^)]*\)", "", right).strip()) for wrong, right in pairs]
    pairs = [(wrong, right) for wrong, right in pairs if wrong != right and right]
    # One printed form has one record name; a form that matches two people is left as printed.
    targets: dict[str, set[str]] = {}
    for wrong, right in pairs:
        targets.setdefault(wrong, set()).add(right)
    # A lone word with no title is changed only when the meeting also names that person with
    # a title or in full ("Leaf" after "Commissioner Leaf" is Leef), or when it sounds like the
    # surname of a member of the meeting's own body on its roster and the meeting never puts
    # it after "the", "a" or the like: a roll call's "Trusty Joselyn" is Trustee Joslin, but
    # "the Burning" is not Trustee Browning. A word that is already a whole part of the
    # surname is a name of its own, not a mishearing: "Connor" may be someone named Connor,
    # not Council Member O'Connor.
    named = {right for wrong, right in pairs if " " in wrong.strip()}
    board = {person.last for person in people.persons if person.on_board and person.last}
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for wrong, right in pairs:
        if wrong in seen or len(targets[wrong]) > 1:
            continue
        words = wrong.split()
        # Two words with no title, each one the meeting also says as an ordinary word:
        # "Sharing Housing" beside "sharing" and "housing" is a group, not Karin Hoskin.
        if (
            len(words) > 1
            and words[0].lower() != right.split()[0].lower()
            and all(word.strip(",.;:?!").lower() in ordinary for word in words)
        ):
            continue
        if len(words) == 1:
            if wrong.lower() in ordinary:
                continue
            if not any(right in full.split() for full in named) and (
                right not in board
                or _after_a_determiner(wrong, texts)
                or wrong in re.split(r"[-'’]", right)
            ):
                continue
        seen.add(wrong)
        out.append({"from": wrong, "to": right})
    return out


_DETERMINER = r"(?i:the|a|an|this|that|these|those|our|their|his|her|its|my|your|some|any|no|every)"


def _after_a_determiner(word: str, texts: list[str]) -> bool:
    """The meeting says ``word`` after "the", "a", "our" and the like somewhere: a thing, not
    a person addressed by surname."""
    pattern = re.compile(rf"(?<!\w){_DETERMINER}\s+{re.escape(word)}(?!\w)")
    return any(pattern.search(text) for text in texts)


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
    evidence, _ = _story_recap(meeting_dir, recap)
    segments = transcript_mod.from_json(data)
    article = _article(article_dir, recap, data, segments, meeting_dir / "documents.json", evidence)
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
                for c in evidence.claims
            ]
        },
        "video_url": _video_url(data),
        "media_url": _media_url(data),
        "article": article,
        "name_fixes": _name_fixes(
            recap,
            _people_of(meeting_dir, meeting_dir / "documents.json"),
            [s.text for s in segments],
        ),
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
    evidence, _ = _story_recap(meeting_dir, recap)
    script = load_script(script_json)
    _same("recap", (item_dir / "recap.txt").read_text(encoding="utf-8"), recap_text(recap))
    # An export made before the label was removed still carries the old line as its second
    # line; parse_txt drops it, so the comparison is against the words that would publish.
    exported = parse_txt((item_dir / "script.txt").read_text(encoding="utf-8"))
    _same("script", render_txt(exported), render_txt(script))

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
        # A roundup's bodies joined run past the site's limit and would each make a board.
        "board": "Weekly roundup" if roundup else recap.body,
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
                for c in evidence.claims
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
        "media_url": None if roundup else _media_url(data),
        "name_fixes": _name_fixes(
            recap,
            _people_of(meeting_dir, meeting_dir / "documents.json", roundup),
            [] if roundup else [s.text for s in segments],
        ),
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
            article_dir, recap, data, segments, meeting_dir / "documents.json", evidence
        )
    return episode


#: A real YouTube video id is 11 characters; "civicclerk-superiorco-42" is not one.
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _video_url(data: dict[str, Any]) -> str | None:
    """The meeting video: the capture's own link, else its media address.

    A CivicClerk, eScribe or Vimeo meeting has no YouTube address; making one from its id
    (``youtube.com/watch?v=civicclerk-superiorco-43``) is a dead link. Without a real video the
    site gets None, and cites fall back to the meeting's documents.
    """
    source = data.get("source") or {}
    if source.get("video_url"):
        return str(source["video_url"])
    media = _media_url(data)
    if media:
        return media
    video_id = str(data.get("video_id") or "")
    if source.get("platform") == "youtube" or _YOUTUBE_ID.match(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    return None


def _media_url(data: dict[str, Any]) -> str | None:
    """The source's own media address, for a meeting that is not on YouTube. None otherwise."""
    source = data.get("source") or {}
    return str(source.get("media_url") or "") or None


def _speaker(segment: transcript_mod.Segment, names: dict[str, str]) -> str:
    if segment.speaker and segment.speaker != UNKNOWN:
        return segment.speaker
    named = names.get(segment.voice or "", UNKNOWN)
    return named if named != UNKNOWN else UNKNOWN


def meeting_name_fixes(meeting_dir: Path) -> list[dict[str, str]]:
    """The name fixes a site file of this meeting gets, from the meeting's records as they are
    now: the same pairs ``assemble`` and ``assemble_story`` write."""
    recap, data = _meeting(meeting_dir)
    roundup = recap.video_id == ROUNDUP_ID
    segments = [] if roundup else transcript_mod.from_json(data)
    return _name_fixes(
        recap,
        _people_of(meeting_dir, meeting_dir / "documents.json", roundup),
        [s.text for s in segments],
    )


def refresh_name_fixes(site_file: Path, meeting_dir: Path) -> bool:
    """Set a built episode or story file's name fixes to what the current code and records give.

    A file is built again only when its desk item changes, so a better name match (or a roster
    corrected since) would never reach a published meeting. Only ``name_fixes`` is rewritten:
    the publish time, audio and text stay as approved. Returns whether the file changed."""
    episode = json.loads(site_file.read_text(encoding="utf-8"))
    pairs = meeting_name_fixes(meeting_dir)
    if episode.get("name_fixes") == pairs:
        return False
    episode["name_fixes"] = pairs
    site_file.write_text(json.dumps(episode, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True
