"""The recap: the only facts a script may use.

A recap is a list of claims. Each claim has the text of one fact, the transcript timestamp where
it starts, and the exact caption words that support it. ``Transcript`` and ``verify_draft`` check
that every quote appears verbatim in the transcript and that every number in a claim appears in
its quote. Claims that fail either check are dropped, never repaired.
"""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from civiccast_brief.script.numbers import number_atoms

HEADER_TIMESTAMP = "0:00:00"
DEFAULT_VIDEO_PLACE = "YouTube channel"
GRANICUS_VIDEO_PLACE = "website"
_CAPTION_RE = re.compile(r"^(\d{1,2}:\d{2}:\d{2})\s+(.*)$")
_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
_NAME_RE = re.compile(r"\b[A-Z][A-Za-z]+(?:'[A-Za-z]+)?\b")
_QUOTE_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")


@dataclass(frozen=True)
class Claim:
    id: str
    topic: str
    text: str
    timestamp: str
    quote: str


@dataclass(frozen=True)
class Recap:
    city: str
    body: str
    meeting_date: str
    video_id: str
    claims: tuple[Claim, ...]
    video_place: str = DEFAULT_VIDEO_PLACE
    #: The meeting's own media address for a meeting that is not on YouTube (PR #99's source
    #: data). Its presence means the video is not on YouTube, so no youtube.com link is made.
    media_url: str | None = None
    #: A weekly roundup's bodies each named with the place it serves, when they are not all the
    #: site's city's boards: "Boulder Valley Board of Education and Louisville City Council".
    #: Empty for one meeting and for a roundup of one city's own bodies, whose labels are
    #: ``body`` with the city, exactly as before.
    body_label: str = ""

    def claim(self, claim_id: str) -> Claim | None:
        for claim in self.claims:
            if claim.id == claim_id:
                return claim
        return None


class RecapFormatError(ValueError):
    """The recap file is not a valid recap."""


def recap_to_dict(recap: Recap) -> dict[str, Any]:
    return {
        "city": recap.city,
        "body": recap.body,
        **({"body_label": recap.body_label} if recap.body_label else {}),
        "meeting_date": recap.meeting_date,
        "video_id": recap.video_id,
        "video_place": recap.video_place,
        **({"media_url": recap.media_url} if recap.media_url else {}),
        "claims": [
            {
                "id": c.id,
                "topic": c.topic,
                "text": c.text,
                "timestamp": c.timestamp,
                "quote": c.quote,
            }
            for c in recap.claims
        ],
    }


def recap_from_dict(data: dict[str, Any]) -> Recap:
    try:
        claims = tuple(
            Claim(
                id=str(item["id"]),
                topic=str(item["topic"]),
                text=str(item["text"]),
                timestamp=str(item["timestamp"]),
                quote=str(item["quote"]),
            )
            for item in data["claims"]
        )
        recap = Recap(
            city=str(data["city"]),
            body=str(data["body"]),
            meeting_date=str(data["meeting_date"]),
            video_id=str(data["video_id"]),
            claims=claims,
            video_place=str(data.get("video_place") or DEFAULT_VIDEO_PLACE),
            media_url=str(data["media_url"]) if data.get("media_url") else None,
            body_label=str(data.get("body_label") or ""),
        )
    except (KeyError, TypeError) as exc:
        raise RecapFormatError(f"recap is missing a field: {exc}") from exc
    _validate(recap)
    return recap


def _validate(recap: Recap) -> None:
    seen: set[str] = set()
    for claim in recap.claims:
        if not claim.id or claim.id in seen:
            raise RecapFormatError(f"claim id missing or repeated: {claim.id!r}")
        seen.add(claim.id)
        if not claim.text.strip() or not claim.quote.strip():
            raise RecapFormatError(f"claim {claim.id} has empty text or quote")
        if not _TIMESTAMP_RE.match(claim.timestamp):
            raise RecapFormatError(f"claim {claim.id} has a bad timestamp {claim.timestamp!r}")


def load_recap(path: Path) -> Recap:
    return recap_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def save_recap(recap: Recap, path: Path) -> None:
    Path(path).write_text(
        json.dumps(recap_to_dict(recap), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def named_bodies(recap: Recap) -> str:
    """The bodies as a label names them: a roundup's ``body_label`` when it has one, else
    ``body``. "Board of Education and City Council" becomes "Boulder Valley Board of Education
    and Louisville City Council" for a roundup that carries another city's board."""
    return recap.body_label or recap.body


def named_place(recap: Recap) -> str:
    """The bodies with the city first: "Louisville City Council", or a roundup's label, whose
    bodies each carry their own place and so need no city put in front."""
    return recap.body_label or f"{recap.city} {recap.body}"


def named_meeting(recap: Recap) -> str:
    """The bodies with their place, for a heading or a prompt: "City Council of Longmont".
    A roundup whose bodies are not all the site's city's keeps each body's own place from its
    label, so a school board is never given the site's city."""
    return recap.body_label or f"{recap.body} of {recap.city}"


#: Words that name what a board is, not the place it serves. A channel's "Boulder Valley
#: School District Board of Education" names the place "Boulder Valley" for a board whose
#: shared roster is headed "Boulder".
_PLACE_TAIL = frozenset({"school", "district", "county", "city", "town", "village", "of", "the"})


def channel_place(recap: Recap, meeting_dir: Path) -> str:
    """The place the video's channel names before the body, when it is not the header's city.

    From the meeting's own record (``raw/<id>.info.json``): the Boulder Valley School District
    board's channel says "Boulder Valley School District Board of Education", while the roster
    Louisville carries for the board heads its city "Boulder"."""
    body = " ".join(recap.body.split())
    raw = Path(meeting_dir) / "raw"
    if not body or not raw.is_dir():
        return ""
    for info in sorted(raw.glob("*.info.json")):
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        channel = " ".join(str(data.get("channel") or "").split())
        if not channel.casefold().endswith(f" {body.casefold()}"):
            continue
        words = channel[: -len(body)].split()
        while words and words[-1].strip(",.").casefold() in _PLACE_TAIL:
            words.pop()
        place = " ".join(word.strip(",") for word in words).strip()
        if place and place.casefold() != recap.city.casefold():
            return place
    return ""


def seconds_of(timestamp: str) -> int:
    hours, minutes, seconds = (int(part) for part in timestamp.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def normalize_quote(text: str) -> str:
    """Caption words as the transcript index stores them: no speaker marks, single spaces."""
    return " ".join(text.replace(">>", " ").split())


class Transcript:
    """Timestamped caption text, joined into one searchable string.

    Caption lines look like ``0:05:06  Okay, it is 7 o'clock.``. Lines before the first caption
    (the header) are stamped 0:00:00. Speaker marks (">>") are dropped.
    """

    def __init__(self, text: str) -> None:
        pieces: list[tuple[str, str]] = []
        current = HEADER_TIMESTAMP
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            match = _CAPTION_RE.match(line)
            if match:
                current, content = match.group(1), match.group(2)
            else:
                content = line
            words = normalize_quote(content)
            if words:
                pieces.append((current, words))
        self._text = ""
        self._starts: list[int] = []
        self._stamps: list[str] = []
        for stamp, words in pieces:
            if self._text:
                self._text += " "
            self._starts.append(len(self._text))
            self._stamps.append(stamp)
            self._text += words

    @property
    def flat_text(self) -> str:
        return self._text

    def _stamp_at(self, offset: int) -> str:
        index = bisect.bisect_right(self._starts, offset) - 1
        return self._stamps[max(index, 0)]

    def occurrences(self, quote: str) -> list[str]:
        """Timestamp of the caption line where each verbatim occurrence of ``quote`` starts."""
        needle = normalize_quote(quote)
        if not needle:
            return []
        stamps = []
        position = self._text.find(needle)
        while position != -1:
            stamps.append(self._stamp_at(position))
            position = self._text.find(needle, position + 1)
        return stamps


@dataclass(frozen=True)
class Dropped:
    draft_index: int
    text: str
    reason: str


@dataclass
class VerifyReport:
    kept: int = 0
    corrected_timestamps: list[str] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)


def parse_draft_json(answer: str) -> list[dict[str, Any]]:
    """Read the JSON array a model returned, ignoring any text around it."""
    start, end = answer.find("["), answer.rfind("]")
    if start == -1 or end <= start:
        raise RecapFormatError("no JSON array in the model answer")
    data = json.loads(answer[start : end + 1])
    if not isinstance(data, list):
        raise RecapFormatError("model answer is not a JSON array")
    return [item for item in data if isinstance(item, dict)]


def _names_missing_from_quote(text: str, quote: str) -> list[str]:
    """Capitalised names in a claim (not sentence openers) that the quote never uses."""
    quote_words = set(_QUOTE_WORD_RE.findall(quote.lower()))
    missing = []
    for match in _NAME_RE.finditer(text):
        before = text[: match.start()].rstrip()
        if not before or before[-1] in ".!?:":
            continue
        if match.group(0).lower() not in quote_words:
            missing.append(match.group(0))
    return missing


def verify_draft(
    draft: list[dict[str, Any]], transcript: Transcript
) -> tuple[list[Claim], VerifyReport]:
    """Keep the draft claims whose quote is verbatim in the transcript and whose numbers and
    names all appear in the quote. Ids are reassigned c01, c02 ... in draft order.

    A claim's timestamp is set to the caption line where its quote actually starts (the
    occurrence nearest the cited timestamp), and the change is counted in the report.
    """
    report = VerifyReport()
    kept: list[Claim] = []
    for index, item in enumerate(draft):
        text = str(item.get("text", "")).strip()
        quote = normalize_quote(str(item.get("quote", "")))
        topic = str(item.get("topic", "")).strip()
        cited = str(item.get("timestamp", "")).strip()
        if not text or not quote or not _TIMESTAMP_RE.match(cited):
            report.dropped.append(Dropped(index, text, "missing text, quote or timestamp"))
            continue
        stamps = transcript.occurrences(quote)
        if not stamps:
            report.dropped.append(Dropped(index, text, "quote is not verbatim in the transcript"))
            continue
        actual = min(stamps, key=lambda s: abs(seconds_of(s) - seconds_of(cited)))
        missing = sorted(number_atoms(text) - number_atoms(quote))
        if missing:
            report.dropped.append(
                Dropped(index, text, f"number(s) not in the quote: {', '.join(missing)}")
            )
            continue
        names = _names_missing_from_quote(text, quote)
        if names:
            report.dropped.append(
                Dropped(index, text, f"name(s) not in the quote: {', '.join(names)}")
            )
            continue
        if actual != cited:
            report.corrected_timestamps.append(f"{text[:60]} {cited} -> {actual}")
        kept.append(
            Claim(
                id=f"c{len(kept) + 1:02d}",
                topic=topic or "General",
                text=text,
                timestamp=actual,
                quote=quote,
            )
        )
    report.kept = len(kept)
    return kept, report
