"""A vote's names from the clerk's roll call in the video, only when every name is certain.

The minutes are the record of a tally. When a meeting's minutes are not published
yet, the only record of who voted is the clerk's roll call in the video: the
chair says "roll call vote, please", reads each member's name, and each answers
yes or no. Automatic captions mangle those names ("Nair, proton, cowish" for
Mayor Pro Tem Jenn Kaaoush, "Many are leases" for Mayor Mark Lacis), so a name
is used only when it maps to the roster as the meeting's own records print it
(:mod:`civiccast_brief.article.names`), or when the roll call has exactly as
many unread slots as there are unread members and each slot has one certain
phonetic candidate among them. Anything less and the vote is reported without
names: an honest tally with no attribution, never a guessed one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from civiccast_brief.article.names import People, _skeleton

_VOTE = re.compile(r"^\s*(yes|yea|no|nay|abstain|abstained)\b", re.IGNORECASE)
_ROLL_CALL = re.compile(r"\broll[\s-]*call\b", re.IGNORECASE)
#: "Councilmember Foster?" / "Mayor Pro Tem Kaaoush." / "Nair, proton, cowish."
_SOUNDS_LIKE_A_NAME = re.compile(r"^[A-Za-z][A-Za-z'’\-.,?: ]{1,60}$")
#: A member's full name as the records print it: "Council Member Mike Foster".
_PRINTED_MEMBER = re.compile(
    r"\b(?:Council\s*Member|Councilmember|Mayor\s+Pro\s*Tem|Mayor)\s+"
    r"([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)?)"
)


def record_members(roster: object, people: People | None, pages: object = None) -> list[str]:
    """Every member this meeting's roster and records print in full, once per surname.

    The roster file can lag the records (a member added after setup), so a full name
    printed with a title in the documents is a member too. ``pages`` is the documents'
    page map (each page with ``.text``); ``people.text`` is already folded to lowercase.
    """
    from civiccast_brief.weekly.minutes import present_members

    names = [member.name for member in getattr(roster, "members", ())]
    texts = [str(getattr(page, "text", "")) for page in (pages or {}).values()]
    print_names = present_members(texts)
    known_surnames = {name.split()[-1].casefold() for name in print_names}
    if people is not None:
        known_surnames |= {
            person.last.casefold() for person in people.persons if "roster" in (person.source or "")
        }
    for name in print_names:
        if name.split()[-1].casefold() not in {n.split()[-1].casefold() for n in names}:
            names.append(name)
    for text in texts:
        for match in _PRINTED_MEMBER.finditer(text):
            name = " ".join(match.group(1).split())
            if name.split()[-1].casefold() not in known_surnames:
                continue
            if name.split()[-1].casefold() not in {n.split()[-1].casefold() for n in names}:
                names.append(name)
    seen: dict[str, str] = {}
    for name in names:
        seen.setdefault(name.split()[-1].casefold(), name)
    return list(seen.values())


def _canonical(name: str, roster_names: list[str]) -> str | None:
    """The member a record person is, by surname; None when the record has no such member."""
    surname = name.split()[-1].casefold() if name.split() else ""
    hits = [member for member in roster_names if member.split()[-1].casefold() == surname]
    return hits[0] if len(hits) == 1 else None


@dataclass(frozen=True)
class RollCall:
    """One roll call the clerk read: who answered yes and no, and the tally."""

    at: float
    timestamp: str
    yes: tuple[str, ...]
    no: tuple[str, ...]
    names_certain: bool

    @property
    def tally(self) -> str:
        return f"{len(self.yes)}-{len(self.no)}"


@dataclass(frozen=True)
class _Slot:
    at: float
    words: str
    vote: str


def stamp(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 3600}:{(whole % 3600) // 60:02d}:{whole % 60:02d}"


def _chunks(segments: list[object], index: int, stop: float) -> list[_Slot]:
    """The name/answer pairs after a roll-call cue, until the result is announced."""
    slots: list[_Slot] = []
    pending: tuple[float, str] | None = None
    for segment in segments[index + 1 :]:
        at = float(getattr(segment, "start", 0))
        if at > stop:
            break
        text = " ".join(str(getattr(segment, "text", "")).split())
        if not text:
            continue
        vote = _VOTE.match(text)
        if vote and pending is not None:
            slots.append(_Slot(pending[0], pending[1], vote.group(1).lower()))
            pending = None
            continue
        if re.search(
            r"\b(?:passes|passed|carries|carried|unanimous|motion carries)\b", text, re.IGNORECASE
        ):
            if pending is not None:
                slots.append(_Slot(pending[0], pending[1], ""))
                pending = None
            break
        if vote:
            continue
        if _SOUNDS_LIKE_A_NAME.match(text) and not re.search(r"\d", text):
            if pending is not None:
                slots.append(_Slot(pending[0], pending[1], ""))
            pending = (at, text)
    if pending is not None:
        slots.append(_Slot(pending[0], pending[1], ""))
    return slots


def _match(words: str, people: People | None, roster_names: list[str]) -> str | None:
    """The one member a name chunk certainly names, or None."""
    if people is None:
        return None
    for candidate in [words, *re.findall(r"[A-Za-z][A-Za-z'’\-]{2,}", words)]:
        person = people.match(candidate)
        if person is not None:
            canonical = _canonical(person.name, roster_names)
            if canonical is not None:
                return canonical
    found: set[str] = set()
    for word in re.findall(r"[A-Za-z][A-Za-z'’\-]{2,}", words):
        person = people.match(word)
        if person is not None:
            canonical = _canonical(person.name, roster_names)
            if canonical is not None:
                found.add(canonical)
    return next(iter(found)) if len(found) == 1 else None


def _by_sound(words: str, remaining: list[tuple[str, str]]) -> str | None:
    """The one remaining member a garbled chunk sounds like, by name skeleton."""
    skeletons = {
        person: {_skeleton(part.lower()) for part in name.split()} for name, person in remaining
    }
    hits = [
        person
        for person, forms in skeletons.items()
        for word in re.findall(r"[A-Za-z][A-Za-z'’\-]{2,}", words)
        if _skeleton(word.lower()) in forms
    ]
    unique = list(dict.fromkeys(hits))
    return unique[0] if len(unique) == 1 else None


def read_roll_call(
    segments: list[object], people: People | None, roster_names: list[str], window_s: float = 90.0
) -> list[RollCall]:
    """Every roll call after a "roll call" cue whose names all map with certainty."""
    calls: list[RollCall] = []
    for index, segment in enumerate(segments):
        text = str(getattr(segment, "text", ""))
        if not _ROLL_CALL.search(text):
            continue
        at = float(getattr(segment, "start", 0))
        slots = [s for s in _chunks(segments, index, at + window_s) if s.vote]
        if not slots:
            continue
        assigned: dict[int, str] = {}
        used: set[str] = set()
        for number, slot in enumerate(slots):
            name = _match(slot.words, people, roster_names)
            if name is not None and name not in used:
                assigned[number] = name
                used.add(name)
        remaining_members = [(n, n) for n in roster_names if n not in used]
        open_slots = [n for n in range(len(slots)) if n not in assigned]
        if open_slots:
            # Only a roll call that names exactly the members still unread can be read by
            # elimination; anything else leaves names out.
            if len(open_slots) != len(remaining_members):
                continue
            for number in open_slots:
                name = _by_sound(slots[number].words, remaining_members)
                if name is None:
                    break
                assigned[number] = name
                remaining_members = [(n, p) for n, p in remaining_members if p != name]
            else:
                pass
            if len(assigned) != len(slots):
                continue
        yes = tuple(assigned[i] for i, s in enumerate(slots) if s.vote in ("yes", "yea"))
        no = tuple(assigned[i] for i, s in enumerate(slots) if s.vote in ("no", "nay"))
        calls.append(RollCall(at, stamp(at), yes, no, True))
    return calls


def roll_call_text(calls: list[RollCall]) -> str:
    """The roll calls as lines the writer reads, in the video's order."""
    lines = []
    for call in calls:
        names = ", ".join(call.yes) if call.yes else "none"
        no = f"; no: {', '.join(call.no)}" if call.no else ""
        lines.append(
            f"- Clerk's roll call in the video at {call.timestamp}: {call.tally} "
            f"(yes: {names}{no})."
        )
    return "\n".join(lines)
