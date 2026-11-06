"""Speaker turns, and roll-call speaker names. Anything not confidently named stays unknown.

Turns come from the ``>>`` marks in the captions: each mark starts a new speaker turn. When
diarization runs (diarize.py), each caption line also carries a voice label; a voice label is not
a name and never becomes one here.

A name is given only by the roll call. A chair's call that matches exactly one roster member (the
last name, plus the first name when the call gives one, plus "Mayor" only for the mayor), followed
by a short acknowledgment in the next turn ("present", "here", "yes"), names the member who
answered. Any other turn stays unknown: the calls that do not match are listed in the report, so a
reader can see what was not named and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from civiccast_brief.meeting.roster import Member, Roster
from civiccast_brief.meeting.transcript import Segment, hms

UNKNOWN = "unknown"
MAX_ACK_GAP_S = 20.0
_TOKEN_RE = re.compile(r"[a-z][a-z'\-]*")
_ACK_WORDS = frozenset({"present", "here", "yes", "aye", "yep", "yeah", "am", "im", "i'm", "i"})
_ACK_NEEDS_ONE = frozenset({"present", "here", "yes", "aye", "yep", "yeah", "am"})
#: Member titles other bodies use before a name ("Supervisor Gauthier", "Vice President Mueller").
#: They are skipped like "council member"; they never name anyone on their own.
OTHER_TITLES = frozenset(
    {
        "supervisor", "commissioner", "trustee", "alderman", "alderwoman", "alderperson",
        "councilor", "councillor", "director", "president", "chair", "chairman", "chairwoman",
        "chairperson",
    }
)  # fmt: skip
DIARIZATION_NOT_REQUESTED = {
    "status": "not run",
    "reason": "diarization was not requested for this run (use --diarize)",
}


@dataclass
class Turn:
    index: int
    first: int
    last: int
    start: float
    end: float
    texts: list[str] = field(default_factory=list)
    speaker: str | None = None
    basis: str = "none"
    #: A title the roll call gives without a matched name ("council member"). It is not a name.
    title: str | None = None

    @property
    def text(self) -> str:
        return " ".join(self.texts)


def split_turns(segments: list[Segment]) -> list[Turn]:
    """Group caption lines into speaker turns. A ``turn_start`` line opens a new turn. Lines before
    the first mark (or a transcript with no marks at all) form turn 0 with no speaker."""
    turns: list[Turn] = []
    for position, segment in enumerate(segments):
        if segment.turn_start or not turns:
            turns.append(
                Turn(
                    index=len(turns),
                    first=position,
                    last=position,
                    start=segment.start,
                    end=segment.end,
                    texts=[segment.text],
                )
            )
        else:
            turn = turns[-1]
            turn.last = position
            turn.end = segment.end
            turn.texts.append(segment.text)
    return turns


@dataclass(frozen=True)
class Call:
    mayor_title: bool
    council_title: bool
    first: str | None
    last: str


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(
        text.lower().replace("pro-tem", "pro tem").replace("councilmember", "council member")
    )


def parse_call(text: str) -> Call | None:
    """A turn that is only a roll-call name: an optional title, then one or two name words.

    "Council member Popkin", "Mayor Susie Hidalgo-Fahring" and "SUPERVISOR GAUTHIER?" parse. "Mayor Prom McCoy" parses
    too (first word "prom"); it fails later when it matches the roster. A turn with more words
    ("Council member Pto is walking in the door") is not a call.
    """
    tokens = _tokens(text)
    i = 0
    mayor_title = False
    council_title = False
    if tokens[i : i + 3] == ["mayor", "pro", "tem"]:
        i += 3
    elif tokens[i : i + 1] == ["mayor"]:
        mayor_title = True
        i += 1
    if tokens[i : i + 2] == ["council", "member"]:
        council_title = True
        i += 2
    elif not mayor_title:
        if (
            tokens[i : i + 1] == ["vice"]
            and tokens[i + 1 : i + 2]
            and tokens[i + 1] in OTHER_TITLES
        ):
            i += 2
        elif tokens[i : i + 1] and tokens[i] in OTHER_TITLES:
            i += 1
    rest = tokens[i:]
    if not 1 <= len(rest) <= 2:
        return None
    first = rest[0] if len(rest) == 2 else None
    return Call(
        mayor_title=mayor_title,
        council_title=council_title,
        first=first,
        last=rest[-1],
    )


def match_member(call: Call, roster: Roster) -> Member | None:
    """The one roster member the call names, or None. Exact spelling only, no fuzzy matching."""
    candidates = [m for m in roster.members if m.last.lower() == call.last]
    if call.first is not None:
        candidates = [m for m in candidates if m.first.lower() == call.first]
    if call.mayor_title:
        candidates = [m for m in candidates if m.role == "Mayor"]
    return candidates[0] if len(candidates) == 1 else None


def is_acknowledgment(text: str) -> bool:
    tokens = _tokens(text)
    return (
        1 <= len(tokens) <= 3
        and all(t in _ACK_WORDS for t in tokens)
        and any(t in _ACK_NEEDS_ONE for t in tokens)
    )


def label_roll_call(turns: list[Turn], roster: Roster) -> dict[str, Any]:
    """Name the turns that answer a matched roll-call call. Returns the report of every call."""
    calls: list[dict[str, Any]] = []
    for k, turn in enumerate(turns[:-1]):
        call = parse_call(turn.text)
        if call is None:
            continue
        member = match_member(call, roster)
        following = turns[k + 1]
        entry: dict[str, Any] = {
            "turn": turn.index,
            "start": hms(turn.start),
            "call_text": turn.text[:80],
            "matched": member.name if member else None,
            "answer_turn": following.index,
            "answer_text": following.text[:40],
        }
        if not is_acknowledgment(following.text):
            entry["status"] = "next turn is not a roll-call answer, so not named"
        elif following.start - turn.end > MAX_ACK_GAP_S:
            entry["status"] = f"answer came more than {MAX_ACK_GAP_S:g} s after the call"
        elif member is not None:
            following.speaker = member.name
            following.basis = "roll_call"
            entry["status"] = "named: answer turn"
        elif call.council_title and not call.mayor_title:
            # The chair called a council member but the name does not match the roster.
            # The answer is a council member's; the name stays unknown.
            following.title = "council member"
            following.basis = "roll_call_title_only"
            entry["status"] = "title only: council member, name not on the roster"
        else:
            entry["status"] = "no roster member matches this call"
        calls.append(entry)
    return {"method": "roll_call", "calls": calls}


def apply_turns(segments: list[Segment], turns: list[Turn]) -> None:
    """Write each turn's speaker onto its caption lines. A turn with no matched name is UNKNOWN,
    including a turn that only has a title (the title is kept in speakers.json)."""
    for turn in turns:
        label = turn.speaker or UNKNOWN
        for position in range(turn.first, turn.last + 1):
            segments[position].speaker = label


def _turn_voice(segments: list[Segment], turn: Turn) -> str | None:
    """The voice on most of the turn's caption lines, or None when no line has one."""
    counts: dict[str, int] = {}
    for position in range(turn.first, turn.last + 1):
        voice = segments[position].voice
        if voice:
            counts[voice] = counts.get(voice, 0) + 1
    return max(sorted(counts), key=lambda v: counts[v]) if counts else None


def speakers_report(
    video_id: str,
    segments: list[Segment],
    turns: list[Turn],
    roll_call: dict[str, Any],
    diarization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    named_segments = sum(turn.last - turn.first + 1 for turn in turns if turn.speaker is not None)
    title_only_segments = sum(
        turn.last - turn.first + 1
        for turn in turns
        if turn.speaker is None and turn.title is not None
    )
    total = len(segments)
    return {
        "video_id": video_id,
        "diarization": diarization or DIARIZATION_NOT_REQUESTED,
        "method": (
            "names: roll call only; turns: caption turn marks; "
            + (
                "voices: textflowkit diarization"
                if (diarization or {}).get("status") == "run"
                else "voices: none"
            )
        ),
        "roll_call": roll_call,
        "turns": [
            {
                "turn": turn.index,
                "start": hms(turn.start),
                "end": hms(turn.end),
                "first_segment": turn.first,
                "last_segment": turn.last,
                "speaker": turn.speaker or UNKNOWN,
                "voice": _turn_voice(segments, turn),
                "title": turn.title,
                "basis": turn.basis,
                "text": turn.text[:120],
            }
            for turn in turns
        ],
        "counts": {
            "segments": total,
            "named_segments": named_segments,
            "title_only_segments": title_only_segments,
            "unknown_segments": total - named_segments,
            "named_percent": round(100 * named_segments / total, 2) if total else 0.0,
            "turns": len(turns),
            "named_turns": sum(1 for t in turns if t.speaker is not None),
        },
    }
