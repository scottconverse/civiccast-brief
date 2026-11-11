"""The deterministic verifier: drop any claim the transcript does not back. Nothing is repaired.

A draft claim is kept only if all of these hold:

1. Its quote appears verbatim in the transcript (same words, capitals and punctuation; caption
   lines may be joined) at a time within WINDOW_S seconds of the cited timestamp.
2. Every number in its text appears in its quote (number words count as digits on both sides).
3. Every roster member the text names is backed: the quote contains that person's last name, or the
   caption line where the quote starts is labelled with that person's name (from the roll call).
4. Every name after a title ("Mayor", "Council member") is on the roster.
5. Every capitalised word in the text is in the official agenda or roster, or is a generic word.
   A misheard board or street name ("Gulf Course") is therefore dropped.

Kept claims take the timestamp of the caption line where their quote starts. Claims are numbered
c01, c02 ... in time order.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from civiccast_brief.meeting.agenda import GENERIC_WORDS
from civiccast_brief.meeting.roster import Roster
from civiccast_brief.meeting.transcript import Segment, hms, normalize_quote, seconds_of
from civiccast_brief.script.recap import Claim

WINDOW_S = 60.0
#: Role words a claim may use only when the quote says them, or when the speaker key gives the
#: title (the roll call gives "council member" for an unmatched name).
ROLE_PHRASES = (
    "city manager",
    "presiding officer",
    "city attorney",
    "mayor",
    "council member",
    "councilmember",
    "staff member",
    "staff",
    "resident",
    "applicant",
    "representative",
    "commenter",
    "presenter",
)
COUNCIL_TITLES = frozenset({"council member", "councilmember"})
_TITLED_NAME_RE = re.compile(
    r"\b(?:Mayor|Council\s+[Mm]ember|Councilmember)\s+"
    r"((?:[A-Z][A-Za-z'\-]+)(?:\s+[A-Z][A-Za-z'\-]+)?)"
)
_CAP_WORD_RE = re.compile(r"\b[A-Z][A-Za-z'\-]*\b")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}


@dataclass(frozen=True)
class Occurrence:
    first: int
    last: int
    seconds: float


class TranscriptIndex:
    """The transcript as one string, with each caption line's position, so a quote can be found
    and its caption line named."""

    def __init__(self, segments: list[Segment]) -> None:
        self._segments = segments
        self._starts: list[int] = []
        pieces: list[str] = []
        offset = 0
        for segment in segments:
            self._starts.append(offset)
            pieces.append(segment.text)
            offset += len(segment.text) + 1
        self.flat = " ".join(pieces)

    def occurrences(self, quote: str) -> list[Occurrence]:
        if not quote:
            return []
        found: list[Occurrence] = []
        position = self.flat.find(quote)
        while position != -1:
            first = bisect.bisect_right(self._starts, position) - 1
            last = bisect.bisect_right(self._starts, position + len(quote) - 1) - 1
            found.append(Occurrence(first=first, last=last, seconds=self._segments[first].start))
            position = self.flat.find(quote, position + 1)
        return found


def _number_words_value(words: list[str]) -> int:
    total = 0
    current = 0
    for word in words:
        if word in _UNITS:
            current += _UNITS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        else:
            total += (current or 1) * _SCALES[word]
            current = 0
    return total + current


def numbers_in(text: str) -> set[str]:
    """Numbers in a text, as digit strings. Digits and number words both count."""
    found = {m.group(0).replace(",", "").rstrip(".") for m in _NUMBER_RE.finditer(text)}
    group: list[str] = []
    for word in re.findall(r"[a-z]+", text.lower()) + [""]:
        if word in _UNITS or word in _SCALES:
            group.append(word)
            continue
        if group:
            found.add(str(_number_words_value(group)))
            group = []
    return found


def _capitalised_words(text: str) -> list[str]:
    """Capitalised words, except the first word of the text (a sentence opener)."""
    return [m.group(0) for m in _CAP_WORD_RE.finditer(text)][1:]


def verify(
    draft: list[dict[str, Any]],
    index: TranscriptIndex,
    segments: list[Segment],
    roster: Roster,
    vocabulary: frozenset[str],
    speaker_at: Callable[[int], str | None] | None = None,
    title_at: Callable[[int], str | None] | None = None,
) -> tuple[list[Claim], dict[str, Any]]:
    """Return the kept claims and a report that lists each dropped draft item with its reason."""
    roster_tokens = {t.lower() for m in roster.members for t in (m.first, m.last)}
    roster_tokens |= {t.lower() for m in roster.members for t in m.name.split()}
    roster_tokens |= {t.lower() for t in f"{roster.city} {roster.state}".split()}
    allowed = vocabulary | {w.lower() for w in GENERIC_WORDS} | roster_tokens
    dropped: list[dict[str, Any]] = []
    kept: list[tuple[float, str, str, str, str]] = []
    corrected: list[str] = []
    reason_counts: dict[str, int] = {}

    def drop(position: int, text: str, reason: str) -> None:
        dropped.append({"draft_index": position, "text": text, "reason": reason})
        key = reason.split(":")[0]
        reason_counts[key] = reason_counts.get(key, 0) + 1

    # The prompt shows "[Name] " before named lines. A quote that copies that tag is still
    # verbatim once the tag is removed. Only speaker names are removed: captions also hold
    # "[laughter]" and the like, which are real transcript text.
    names = sorted({s.speaker for s in segments if s.speaker and s.speaker != "unknown"})
    name_tag = (
        re.compile(r"\[(?:" + "|".join(re.escape(n) for n in names) + r")\]\s*") if names else None
    )

    for position, item in enumerate(draft):
        text = str(item.get("text", "")).strip()
        topic = str(item.get("topic", "")).strip() or "General"
        cited = str(item.get("timestamp", "")).strip()
        raw_quote = str(item.get("quote", ""))
        if name_tag is not None:
            raw_quote = name_tag.sub("", raw_quote)
        quote = normalize_quote(raw_quote)
        if not text or not quote or not re.match(r"^\d{1,2}:\d{2}:\d{2}$", cited):
            drop(position, text, "missing text, quote or timestamp")
            continue
        occurrences = index.occurrences(quote)
        if not occurrences:
            drop(position, text, "quote is not verbatim in the transcript")
            continue
        cited_s = seconds_of(cited)
        nearest = min(occurrences, key=lambda o: abs(o.seconds - cited_s))
        gap = abs(nearest.seconds - cited_s)
        if gap > WINDOW_S:
            drop(position, text, f"quote is verbatim but too far from the cited time: {gap:.0f} s")
            continue
        missing = sorted(numbers_in(text) - numbers_in(quote))
        if missing:
            drop(position, text, f"number not in the quote: {', '.join(missing)}")
            continue
        quote_lower = quote.lower()
        quote_words = set(re.findall(r"[a-z][a-z'\-]*", quote_lower))
        unbacked = []
        for member in roster.members:
            mentioned = re.search(rf"\b{re.escape(member.last)}\b", text, re.IGNORECASE)
            if not mentioned:
                continue
            spoken_by = speaker_at(nearest.first) if speaker_at else None
            if member.last.lower() not in quote_words and spoken_by != member.name:
                unbacked.append(member.name)
        if unbacked:
            drop(
                position,
                text,
                "names a person the quote and speaker label do not support: " + ", ".join(unbacked),
            )
            continue
        off_roster = []
        for match in _TITLED_NAME_RE.finditer(text):
            for token in match.group(1).split():
                if token.lower() not in roster_tokens:
                    off_roster.append(token)
        if off_roster:
            drop(position, text, f"titled name not on the roster: {', '.join(off_roster)}")
            continue
        unbacked_roles = []
        for phrase in ROLE_PHRASES:
            if not re.search(rf"\b{phrase}\b", text, re.IGNORECASE):
                continue
            if phrase in quote_lower:
                continue
            if (
                phrase in COUNCIL_TITLES
                and title_at
                and title_at(nearest.first) == "council member"
            ):
                continue
            unbacked_roles.append(phrase)
        if unbacked_roles:
            drop(
                position,
                text,
                "names a role the quote and speaker key do not give: " + ", ".join(unbacked_roles),
            )
            continue
        # The speaker label's name counts on that speaker's own line, so a person named from
        # their own introduction can be named in a claim that quotes them, and nowhere else.
        spoken_by = speaker_at(nearest.first) if speaker_at else None
        allowed_here = allowed
        if spoken_by and spoken_by != "unknown":
            allowed_here = allowed | {t.lower() for t in spoken_by.split()}
        off_agenda = sorted(
            {w for w in _capitalised_words(text) if len(w) > 1 and w.lower() not in allowed_here}
        )
        if off_agenda:
            drop(
                position,
                text,
                "term not in the official agenda or roster: " + ", ".join(off_agenda),
            )
            continue
        stamp = hms(nearest.seconds)
        if stamp != cited:
            corrected.append(f"{text[:60]} {cited} -> {stamp}")
        kept.append((nearest.seconds, topic, text, stamp, quote))

    kept.sort(key=lambda entry: entry[0])
    claims = [
        Claim(id=f"c{number:02d}", topic=topic, text=text, timestamp=stamp, quote=quote)
        for number, (_, topic, text, stamp, quote) in enumerate(kept, start=1)
    ]
    report: dict[str, Any] = {
        "draft_items": len(draft),
        "kept": len(claims),
        "dropped": len(dropped),
        "window_seconds": WINDOW_S,
        "drop_reasons": reason_counts,
        "timestamp_corrections": corrected,
        "dropped_items": dropped,
    }
    return claims, report
