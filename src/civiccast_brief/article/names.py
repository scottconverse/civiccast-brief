"""A printed person name must match the record, and print the record's spelling.

Speech-to-text mangles names ("Mahanni Hippoli" for the packet's "Hannah Hippely", "Mayor
Pro Tem Cowish" for the roster's "Jenn Kaaoush"). A name is allowed in a story only when it
matches a name in the meeting's records: the city rosters (``roster.json`` and
``rosters/*.json``) or the meeting's own documents (``documents.json``: the agenda, packet and
minutes). The match is fuzzy, by surname sound or spelling and by role, but the story always
prints the record's spelling. A name heard only in the audio with no record match is never
printed: the story uses the role instead ("a council member", "a resident").

The check runs after the writer, on every place a name is printed: the headline, dek, section
headings, paragraphs, vote lines and quote attributions. A story with a wrong spelling fails
and :func:`repair_article` replaces the name with the record's spelling or with a role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

#: Roles that may stand in front of a name: "Mayor Pro Tem X", "Council Member X", "Director X".
ROLE_WORDS = (
    "Mayor",
    "Pro",
    "Tem",
    "ProTem",
    "Pro-tem",
    "Council",
    "Member",
    "Councilmember",
    "President",
    "Vice",
    "Trustee",
    "Commissioner",
    "Town",
    "Assistant",
    "Asst",
    "Deputy",
    "Manager",
    "Director",
    "Clerk",
    "Attorney",
    "Chief",
    "Officer",
    "Superintendent",
    "Administrator",
    "Supervisor",
    "Chair",
    "Chairperson",
)
_ROLE_WORD_SET = {word.lower() for word in ROLE_WORDS}
#: Words that start a sentence but are never a person's name.
_OPENERS = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "also",
    "later",
    "earlier",
    "during",
    "following",
    "before",
    "after",
    "separately",
    "meanwhile",
    "in",
    "on",
    "at",
    "with",
    "under",
    "without",
    "among",
    "several",
    "both",
    "other",
    "others",
    "most",
    "no",
    "none",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "members",
    "speakers",
    "residents",
    "public",
    "he",
    "she",
    "they",
    "it",
    "we",
    "you",
    "i",
    "i'm",
    "i'll",
    "i'd",
    "i've",
    "we're",
    "we'll",
    "we'd",
    "we've",
    "so",
    "okay",
    "hi",
    "hello",
    "thanks",
    "thank",
    "hopefully",
    "again",
    "nothing",
    "awesome",
    "maybe",
    "and",
    "but",
}
#: Capitalised words that are dates, places or institutions, never a person's name alone.
_NOT_PERSON = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "today",
    "tonight",
    "tomorrow",
    "superior",
    "louisville",
    "lafayette",
    "longmont",
    "boulder",
    "colorado",
    "erie",
    "lyons",
    "nederland",
    "denver",
    "town",
    "city",
    "county",
    "state",
    "council",
    "board",
    "commission",
    "committee",
    "department",
    "district",
    "court",
    "hall",
    "chambers",
    "drive",
    "street",
    "avenue",
    "road",
    "park",
    "center",
    "library",
    "school",
    "ordinance",
    "resolution",
    "item",
    "code",
    "act",
    "section",
    "page",
    "exhibit",
    "attachment",
    "packet",
    "agenda",
    "minutes",
    "budget",
    "contract",
    "grant",
    "fund",
    "program",
    "presented",
    "prepared",
    "for",
    "by",
    "staff",
    "items",
    "motion",
    "consent",
    "report",
    "update",
    "presentation",
    "discussion",
    "action",
    "approval",
    "public",
    "comment",
    "meeting",
    "services",
    "service",
    "group",
    "team",
    "office",
    "parks",
}
#: Words that can never be part of a person's name. A document heading that contains one is a
#: title, not a person ("Consent Agenda Motion", "Sheriff's Substation Office").
_NOT_NAME_WORDS = _NOT_PERSON | {
    "sheriff",
    "substation",
    "police",
    "fire",
    "code",
    "station",
    "sheriff's",
}
#: Words that begin a document label, never a person's name: "Resolution R-87" is a resolution.
_DOCUMENT_WORDS = frozenset(
    {
        "resolution",
        "ordinance",
        "item",
        "section",
        "page",
        "exhibit",
        "attachment",
        "agenda",
        "minutes",
        "budget",
        "contract",
        "packet",
        "application",
        "proclamation",
    }
)
#: Document words strong enough that any name run holding one is a label, not a person.
_DOCUMENT_STRONG = frozenset(
    {
        "resolution",
        "ordinance",
        "exhibit",
        "attachment",
        "proclamation",
        "packet",
        "agenda",
        "minutes",
        "budget",
        "contract",
    }
)
_RUN_RE = re.compile(r"\b[A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*)*")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_SENTENCE_START = re.compile(r"(^|[.!?:]\s*)$")


@dataclass(frozen=True)
class Person:
    """A person the record names, with the record's spelling and where it is printed."""

    name: str
    first: str
    last: str
    role: str
    source: str
    evidence: str


@dataclass(frozen=True)
class People:
    """Every person the meeting's records name, and the record text to check against."""

    persons: tuple[Person, ...]
    text: str

    def match(self, printed: str, role: str = "") -> Person | None:
        """The record person the printed name means, or None.

        ``role`` is the title printed in front of the name ("Mayor Pro Tem"), which identifies
        a member whose surname the audio mangled beyond spelling.
        """
        wanted = _tokens(printed)
        if not wanted:
            return None
        best: tuple[float, Person] | None = None
        for person in self.persons:
            score = _score(wanted, person)
            if score and role and _role_key(role) == _role_key(person.role):
                score += 0.001  # break a tie between two similar names
            if score and (best is None or score > best[0]):
                best = (score, person)
        return best[1] if best and best[0] >= 0.62 else None

    def verbatim(self, name: str) -> bool:
        """Whether the record prints this exact sequence (a company, place or document title)."""
        needle = _normal(name)
        return bool(needle) and f" {needle} " in f" {self.text} "


def _normal(text: str) -> str:
    lowered = text.replace("’", "'").replace("–", "-").replace("—", "-").lower()
    return " ".join(re.sub(r"[^a-z0-9'\- ]+", " ", lowered).split())


def _tokens(name: str) -> list[str]:
    return [t for t in _normal(name).replace("-", " ").split() if t]


def _skeleton(word: str) -> str:
    """A rough sound key: consonant classes, vowels dropped.

    "Cowish" and "Kaaoush" share the key "22" (c and k are one class); "Hippoli" and
    "Hippely" share "h14".
    """
    classes = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        "l": "4",
        **dict.fromkeys("mn", "5"),
        "r": "6",
    }
    out = []
    for letter in word:
        code = classes.get(letter)
        if code is None:
            if letter not in "aeiouhwy" and letter.isalpha():
                out.append(letter)
            continue
        if not out or out[-1] != code:
            out.append(code)
    return "".join(out)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _score(wanted: list[str], person: Person) -> float:
    first, last = _tokens(person.first), _tokens(person.last)
    record = _tokens(person.name)
    printed = " ".join(wanted)
    full = _normal(person.name)
    if printed == full:
        return 1.0
    # A surname alone cannot confirm a full name the record never prints in full.
    if len(wanted) >= 2 and len(record) == 1:
        return 0.0
    if len(wanted) == 1:
        word = wanted[0]
        if word in last or word in first or word in record:
            return 0.95
        best = max(
            (_ratio(word, token) for token in [*last, *first] if token),
            default=0.0,
        )
        # A crude sound key collides on short words ("depth" and "deputy"); only a longer
        # surname may match on sound alone.
        skeleton = any(
            _skeleton(word) == _skeleton(token) and min(len(word), len(token)) >= 6
            for token in [*last, *first]
            if token
        )
        if skeleton and best >= 0.4:
            return 0.80
        if best >= 0.72:
            return 0.78
        return 0.0
    first_ratio = max((_ratio(wanted[0], token) for token in first), default=0.0)
    last_ratio = max((_ratio(wanted[-1], token) for token in last), default=0.0)
    # The surname must be close: "Sheriff's Substation Office" is not Jorge Chávez.
    if last_ratio < 0.5:
        return 0.0
    full_ratio = _ratio(printed, full)
    score = 0.30 * first_ratio + 0.45 * last_ratio + 0.25 * full_ratio
    # A surname that sounds the same is the strongest sign of the same person.
    if last_ratio >= 0.55 and any(
        _skeleton(wanted[-1]) == _skeleton(token) and min(len(wanted[-1]), len(token)) >= 6
        for token in last
        if token
    ):
        score = max(score, 0.70)
    # The captions merge a first name into a surname ("Craig Craft" for "Cracraft"). When the
    # surname is nearly the record's and the printed first name matches no other member, the
    # surname is the certain match: the record prints the whole name and the repair uses it.
    if last_ratio >= 0.75 and first_ratio < 0.5:
        score = max(score, 0.72)
    return score


def _role_key(role: str) -> str:
    key = " ".join(_normal(role).replace("-", " ").split())
    # The captions write the seat three ways ("pro tem", "protem", "pro-tem").
    return key.replace("protem", "pro tem")


def _role_phrase(role: str, context: str, at_start: bool) -> str:
    """A role to print instead of a name the record does not hold."""
    key = _normal(role)
    if "planning" in key or "building" in key:
        phrase = "a planning department staff member"
    elif "mayor" in key or "council" in key or "trustee" in key:
        phrase = "a council member"
    elif any(
        word in key
        for word in ("manager", "director", "clerk", "attorney", "chief", "supervisor", "staff")
    ):
        phrase = "town staff"
    elif "public comment" in _normal(context) or "resident" in _normal(context):
        phrase = "a resident"
    else:
        phrase = "a speaker"
    return phrase[:1].upper() + phrase[1:] if at_start else phrase


#: The last word of a street, park or trail name: "Honey Creek Lane" is a place, not a person.
_PLACE_WORDS = frozenset(
    {
        "lane",
        "street",
        "road",
        "drive",
        "avenue",
        "ave",
        "court",
        "ct",
        "place",
        "pl",
        "way",
        "trail",
        "park",
        "circle",
        "boulevard",
        "blvd",
        "parkway",
        "pkwy",
        "highway",
        "hwy",
        "path",
    }
)


def _is_person_like(name: str, role: str) -> bool:
    tokens = _tokens(name)
    if not tokens:
        return False
    if role:
        return True
    if tokens[-1] in _PLACE_WORDS:
        return False
    return len(tokens) >= 2


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    name: str
    role: str
    #: Where the printed role starts, so an unmatched "Mayor X" loses the whole phrase.
    lead_start: int = 0


def spans(text: str) -> list[Span]:
    """The person names printed in a text: runs of capitalised words, with a role in front."""
    found: list[Span] = []
    for match in _RUN_RE.finditer(text):
        words = _WORD_RE.findall(match.group(0))
        if not words:
            continue
        start, end = match.start(), match.end()
        raw = match.group(0)
        # A run that ends in a possessive keeps the name only.
        if raw.endswith(("'s", "’s")):
            raw = raw[:-2]
            end -= 2
        tokens = raw.split()
        while tokens and tokens[0].lower() in _OPENERS:
            tokens.pop(0)
        role_words: list[str] = []
        while tokens and tokens[0].lower() in _ROLE_WORD_SET:
            role_words.append(tokens.pop(0))
        tokens = [t for t in tokens if t.lower() not in _ROLE_WORD_SET]
        if not tokens:
            continue
        # "Ordinance O-2026-59" is a document number, not a person.
        if any(len(t.strip("'’\\-")) <= 1 for t in tokens):
            continue
        if all(t.lower() in _NOT_NAME_WORDS for t in tokens):
            continue
        lowered = {token.lower() for token in tokens}
        if tokens[0].lower() in _DOCUMENT_WORDS or lowered & _DOCUMENT_STRONG:
            continue
        # "Full-depth" is a compound word, not a person: a surname's second half is capitalised.
        if re.search(r"-[a-z]", " ".join(tokens)):
            continue
        role = " ".join(role_words)
        name = " ".join(tokens)
        # Offsets: the run's match is contiguous, so the name starts after the words stripped.
        lead = len(raw) - len(raw.lstrip())
        name_at = raw.find(name)
        if name_at < 0:
            continue
        found.append(
            Span(
                start + lead + name_at,
                start + lead + name_at + len(name),
                name,
                role,
                start + lead,
            )
        )
    return found


def _at_sentence_start(text: str, start: int) -> bool:
    return bool(_SENTENCE_START.search(text[:start]))


def _spelling(span: Span, person: Person) -> str:
    """The record's spelling in the form the story printed: a surname stays a surname, and a
    role in front of the name takes the full record name ("Mayor Pro Tem Jenn Kaaoush")."""
    tokens = _tokens(span.name)
    if span.role:
        return person.name
    if len(tokens) == 1:
        word = tokens[0]
        last = _tokens(person.last)
        first = _tokens(person.first)
        if word in last:
            return person.last
        if word in first:
            return person.first
        if max((_ratio(word, t) for t in last), default=0.0) >= max(
            (_ratio(word, t) for t in first), default=0.0
        ):
            return person.last
        return person.first
    return person.name


def name_issues(text: str, people: People | None) -> list[tuple[str, str]]:
    """(code, message) for each person name the record does not allow as printed."""
    if people is None or not people.persons:
        return []
    out: list[tuple[str, str]] = []
    for span in spans(text):
        person = people.match(span.name, span.role)
        if person is None:
            if _is_person_like(span.name, span.role) and not people.verbatim(span.name):
                out.append(
                    (
                        "name_unknown",
                        f"name {span.name!r} is not in the meeting record; use the role instead",
                    )
                )
            continue
        if people.verbatim(span.name):
            # The record prints this exact sequence ("Rock Creek"); leave a place or company be.
            continue
        if _tokens(span.name) != _tokens(person.name):
            out.append(
                (
                    "name_spelling",
                    (
                        f"name {span.name!r} must be printed as the record prints it: "
                        f"{person.name!r} ({person.source})"
                    ),
                )
            )
    return out


def person_issues(text: str, people: People | None) -> list[tuple[str, str]]:
    """(code, message) for each *member or staff* name the record does not print as written.

    The skill's story carries its own claim-by-claim record, and most capitalised runs in a
    meeting story are places, boards, companies and events ("Community Center", "Superior
    Elementary"). This gate is therefore limited to the people the meeting's roster and
    documents name: a span is judged only when its surname is that person's surname (exactly
    or by the phonetic skeleton), so a caption spelling ("Craigcraft") is caught and an
    organisation ("Northwest Mayors Coalition") is left to the story's own check.
    """
    if people is None or not people.persons:
        return []
    out: list[tuple[str, str]] = []
    for span in spans(text):
        tokens = _tokens(span.name)
        if not tokens:
            continue
        surname = tokens[-1].casefold()
        person = people.match(span.name, span.role)
        if person is None:
            continue
        known = {_skeleton(part.casefold()) for part in person.last.split()} | {
            _normal(person.last)
        }
        if _skeleton(surname) not in known and _normal(surname) != _normal(person.last):
            continue
        if people.verbatim(span.name):
            continue
        if _tokens(span.name) != _tokens(person.name):
            out.append(
                (
                    "name_spelling",
                    (
                        f"name {span.name!r} must be printed as the record prints it: "
                        f"{person.name!r} ({person.source})"
                    ),
                )
            )
    return out


def fixes(text: str, people: People | None) -> list[tuple[str, str]]:
    """The (printed, record) name pairs a text needs, for a display-time replacement.

    The recap and the transcript are printed on the site beside the story. Their names come
    through the same speech-to-text, so the site applies the same corrections. These are
    deliberately conservative: only a name whose surname matches the record is changed, and a
    name the record does not hold is left as the transcript has it. Free transcript text is full
    of ordinary capitalised words, and guessing a role for each would rewrite the record.
    """
    if people is None or not people.persons:
        return []
    out: list[tuple[str, str]] = []
    for span in spans(text):
        person = people.match(span.name, span.role)
        if person is None or people.verbatim(span.name):
            continue
        if _tokens(span.name) == _tokens(person.name):
            continue
        tokens, last = _tokens(span.name), _tokens(person.last)
        if len(tokens) == 1:
            word = tokens[0]
            if len(word) < 4:
                continue
            if not (
                word in last
                or any(
                    _skeleton(word) == _skeleton(token) and _ratio(word, token) >= 0.45
                    for token in last
                    if token
                )
            ):
                continue
        elif max((_ratio(tokens[-1], token) for token in last), default=0.0) < 0.45:
            continue
        out.append((span.name, _spelling(span, person)))
    return out


def repair_text(text: str, people: People | None) -> tuple[str, list[str]]:
    """Replace each record-matched name with the record's spelling, and each unmatched name
    with a role. Returns the text and a line for each change."""
    if people is None or not people.persons:
        return text, []
    changes: list[str] = []
    out = text
    for span in reversed(spans(text)):
        person = people.match(span.name, span.role)
        if person is not None:
            if _tokens(span.name) == _tokens(person.name) or people.verbatim(span.name):
                continue
            spelling = _spelling(span, person)
            out = out[: span.start] + spelling + out[span.end :]
            changes.append(f"name {span.name!r} -> {spelling!r} ({person.source})")
            continue
        if not _is_person_like(span.name, span.role) or people.verbatim(span.name):
            continue
        context = text[max(0, span.start - 60) : span.end + 60]
        role = _role_phrase(span.role, context, _at_sentence_start(text, span.lead_start))
        out = out[: span.lead_start] + role + out[span.end :]
        changes.append(f"name {span.name!r} -> {role!r} (no record match)")
    return out, changes


def repair_article(article: Any, people: People | None) -> tuple[Any, list[str]]:
    """Repair every printed name in a story, in place of :class:`Article` (same shape)."""
    if people is None:
        return article, []
    changes: list[str] = []

    def fix(text: str) -> str:
        new, lines = repair_text(text, people)
        new, seat_lines = fill_roll_call_names(new, people)
        changes.extend(lines)
        changes.extend(seat_lines)
        return new

    sections = tuple(
        replace(
            section,
            heading=fix(section.heading) if section.heading else None,
            paragraphs=tuple(
                replace(paragraph, text=fix(paragraph.text)) for paragraph in section.paragraphs
            ),
        )
        for section in article.sections
    )
    votes = tuple(
        replace(
            vote,
            motion=fix(vote.motion),
            result=fix(vote.result),
            tally=fix(vote.tally) if vote.tally else None,
        )
        for vote in article.votes
    )
    quotes = tuple(replace(quote, speaker=fix(quote.speaker)) for quote in article.quotes)
    return (
        replace(
            article,
            headline=fix(article.headline),
            dek=fix(article.dek),
            sections=sections,
            votes=votes,
            quotes=quotes,
        ),
        changes,
    )


#: A roll call names the seat and not the person ("Mayor Pro Tem: Yes", "and Mayor Pro Tem
#: voted yes"). The roster prints the holder, so the voter is named from the record.
_SEAT_ONLY = {
    "mayor": re.compile(r"(?<!Pro[- ])\bMayor\b(?!\s+Pro[- ]?[Tt]em)(?!\s+[A-Z])"),
}
#: Words that mark a passage as a roll call or a vote.
_VOTE_CONTEXT_RE = re.compile(r"\b(voted?|votes?|roll[- ]call|aye|yes|nay|no)\b", re.IGNORECASE)
#: The mayor pro tem seat, in the three spellings the captions use, with the token after it.
#: The seat as the captions write it: "Mayor Pro Tem", "mayor protem", "Merry proton",
#: "proton, cowish". The optional leading word is the captions' misheard "Mayor".
_MAYOR_PRO_TEM_RE = re.compile(
    r"\b(?:(?:Mayor|Merry)\s+)?Pro[- ]?(?:[Tt]em|[Tt]on)\b"
    r"((?:\s*[,\s]\s*[A-Za-z][A-Za-z'’\-]*){0,2})",
    re.IGNORECASE,
)
#: A word after "Mayor Pro Tem" that is a verb or connector, not a person's name.
_NOT_A_NAME_AFTER_SEAT = frozenset(
    {
        "voted",
        "votes",
        "voting",
        "seconded",
        "second",
        "seconds",
        "moved",
        "moves",
        "spoke",
        "speaks",
        "said",
        "says",
        "noted",
        "reported",
        "asked",
        "asks",
        "called",
        "calls",
        "will",
        "would",
        "was",
        "is",
        "also",
        "and",
        "then",
        "had",
        "has",
        "agreed",
        "announced",
        "announces",
        "presented",
        "presents",
        "read",
        "reads",
        "broke",
        "breaks",
    }
)


def _seat_holder(people: People, role: str) -> Person | None:
    """The record person who holds a seat, preferring a printed first and last name.

    The packet sometimes prints only a surname ("Mayor Pro-tem Kaaoush") beside the roster's
    full name ("Jenn Kaaoush"). The full name is the one to print.
    """
    holders = [p for p in people.persons if _role_key(p.role) == _role_key(role)]
    if not holders:
        return None
    return max(holders, key=lambda p: (len(_tokens(p.name)), p.name))


def _other_member(word: str, person: Person, people: People) -> bool:
    """Whether a word after the seat is another record member's name, not the seat's holder."""
    wanted = _normal(word)
    for other in people.persons:
        if other.name == person.name:
            continue
        if wanted and wanted in (_normal(other.first), _normal(other.last)):
            return True
    return False


def _seat_replacement(tail: str, person: Person, people: People | None = None) -> str:
    """The record's seat and name, keeping the words after it that are not the printed name."""
    record = f"Mayor Pro Tem {person.name}"
    words = [word.strip(",.;:") for word in tail.split() if word.strip(",.;:")]
    if not words:
        return record
    if words[0].lower() in _NOT_A_NAME_AFTER_SEAT:
        return f"{record} {' '.join(words)}"
    first, last = _normal(person.first), _normal(person.last)
    if _normal(words[0]) == first and len(words) > 1 and _normal(words[1]) == last:
        rest = words[2:]
    elif _normal(words[0]) in (first, last):
        rest = words[1:]
    elif people is not None and _other_member(words[0], person, people):
        # Another member is printed after the seat ("Mayor Pro Tem, Stephanie Miller"). The
        # seat takes the record's holder and the other member keeps their name.
        return " ".join([record + ",", *words])
    else:
        # A garbled spelling of the holder. The holder is the record's.
        rest = words[1:]
    return " ".join([record, *rest])


def seat_fixes(text: str, people: People | None) -> list[tuple[str, str]]:
    """(printed, record) pairs that name the mayor pro tem from the roster.

    The seat is held by one member the record prints, so the record's name goes wherever the
    record says "mayor pro tem": a seat-only voter ("Mayor Pro Tem voted yes"), a caption
    spelling ("mayor protem cowish"), or a bare mention ("the mayor pro tem seconded").
    """
    if people is None or not people.persons:
        return []
    person = _seat_holder(people, "Mayor Pro Tem")
    if person is None:
        return []
    out: list[tuple[str, str]] = []
    for match in _MAYOR_PRO_TEM_RE.finditer(text):
        tail = match.group(1) or ""
        replacement = _seat_replacement(tail, person, people)
        if replacement != match.group(0):
            out.append((match.group(0), replacement))
    return out


def apply_fixes(text: str, pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    """Replace each printed name with its record spelling, longest form first.

    A pair whose record spelling starts with the printed form ("Mayor Pro Tem" ->
    "Mayor Pro Tem Jenn Kaaoush") is not applied where that name is already printed, so one
    replacement cannot repeat the name.
    """
    for wrong, right in sorted(pairs, key=lambda pair: -len(pair[0])):
        remainder = right[len(wrong) :] if right.startswith(wrong) else ""
        pattern = re.escape(wrong)
        if remainder:
            pattern += f"(?!{re.escape(remainder)})"
        text = re.sub(pattern, lambda _match, right=right: right, text)
    return text


def fill_roll_call_names(text: str, people: People | None) -> tuple[str, list[str]]:
    """Name the mayor pro tem, and a seat-only mayor at a vote, from the record's roster.

    The mayor pro tem is one record member, so the seat is named wherever it appears. A bare
    "Mayor" is named only in a vote passage: in ordinary prose it may be a presiding officer
    the record does not name.
    """
    if people is None or not people.persons:
        return text, []
    changes: list[str] = []
    person = _seat_holder(people, "Mayor Pro Tem")
    out = text
    if person is not None:

        def seat(match: re.Match[str]) -> str:
            tail = match.group(1) or ""
            replacement = _seat_replacement(tail, person, people)
            if replacement != match.group(0):
                changes.append(f"seat {match.group(0)!r} -> {replacement!r} (record roster)")
            return replacement

        out = _MAYOR_PRO_TEM_RE.sub(seat, out)
    if not _VOTE_CONTEXT_RE.search(text):
        return out, changes
    for seat_name, pattern in _SEAT_ONLY.items():
        mayor = next(
            (p for p in people.persons if _role_key(p.role) == _role_key(seat_name)),
            None,
        )
        if mayor is None:
            continue
        match = pattern.search(out)
        if match is None:
            continue
        out = out[: match.start()] + mayor.name + out[match.end() :]
        changes.append(f"roll call {match.group(0)!r} -> {mayor.name!r} ({mayor.source})")
    return out, changes


#: A presented-by line: "Presented By: Matt Magley, Town Manager, Leslie Clark, ...".
_PRESENTED_RE = re.compile(r"(?:Presented|Prepared)\s+[Bb]y\s*:\s*(.+)", re.IGNORECASE)
#: A role immediately before a name: "Council Member Mike Foster", "Mayor Pro-tem Jenn Kaaoush".
#: A motion line names who moved or seconded: "moved by Alex Kalkhofer".
_MOTION_BY_RE = re.compile(
    r"(?:moved|seconded|motion|made)\s+by\s+"
    r"([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,2})",
    re.IGNORECASE,
)
_ROLE_BEFORE_RE = re.compile(
    r"(Mayor\s+Pro[- ]?[Tt]em|Mayor|Council\s*Member|Council\s+President|Trustee|Commissioner|"
    r"Town\s+Manager|Assistant\s+Town\s+Manager|Asst\.?\s+Town\s+Manager|Deputy\s+Town\s+Clerk|"
    r"Town\s+Clerk|Town\s+Attorney|Director|Chief|Superintendent|Administrator|Supervisor|"
    r"Attorney|Clerk)\s+([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,2})"
)
#: Words that mark a chunk in a "Presented By" line as a role, not a name.
_ROLE_MARKERS = {
    "manager",
    "director",
    "clerk",
    "attorney",
    "chief",
    "officer",
    "supervisor",
    "administrator",
    "superintendent",
    "mayor",
    "council",
    "trustee",
    "commissioner",
}
_NAME_ONLY_RE = re.compile(
    r"^[A-Z][A-Za-z'’\-]+(?:\s+(?:\([A-Za-z]+\)\s+)?[A-Z][A-Za-z'’\-]+){0,2}$"
)


def _is_role_chunk(text: str) -> bool:
    words = _WORD_RE.findall(text)
    return (
        bool(words)
        and words[0][0].isupper()
        and bool({word.lower() for word in words} & _ROLE_MARKERS)
    )


def _name_or_none(text: str, allow_single: bool = True) -> str | None:
    # Drop a parenthesised nickname: "Jennifer (JG) Garner" is printed "Jennifer Garner".
    cleaned = " ".join(re.sub(r"\([A-Za-z]+\)", " ", text).split())
    if not _NAME_ONLY_RE.match(cleaned):
        return None
    words = _WORD_RE.findall(cleaned)
    if not words or all(
        word.lower() in _NOT_NAME_WORDS or word.lower() in _ROLE_WORD_SET for word in words
    ):
        return None
    # A single capitalised word in a presented-by list is a department ("Parks"), not a person.
    if len(words) < 2 and not allow_single:
        return None
    return " ".join(words)


def people_from_documents(pages: dict[str, Any], documents_path: str = "") -> list[Person]:
    """The people the meeting's documents name, with the role and the line that says so."""
    found: dict[str, Person] = {}
    for page in pages.values():
        text = str(page.text or "")
        for presented in _PRESENTED_RE.finditer(text):
            # Read past the first line: the list wraps, and the role often follows on the next.
            after = text[presented.start() :]
            colon = after.find(":")
            after = after[colon + 1 :] if colon != -1 else after
            stop = re.search(r"Presented\s+For|Agenda\s+Item\s+Name|Background\s*:", after)
            segment = after[: stop.start()] if stop else after[:500]
            line = " ".join(segment.split())
            _presented_chunks(found, segment, page, documents_path, line)
        for match in _ROLE_BEFORE_RE.finditer(text):
            role, raw_name = match.group(1), match.group(2)
            name = _name_or_none(raw_name)
            if name is not None:
                _add(found, name, role, page, documents_path, _line_at(text, match.start()))
        for match in _MOTION_BY_RE.finditer(text):
            name = _name_or_none(match.group(1))
            if name is not None:
                _add(found, name, "", page, documents_path, _line_at(text, match.start()))
    return list(found.values())


def _presented_chunks(
    found: dict[str, Person], segment: str, page: Any, documents_path: str, line: str
) -> None:
    """Read "Name, Role, Name, Role" pairs; a role may follow its name or come before it."""
    chunks = [" ".join(chunk.split()) for chunk in segment.split(",")]
    pairs: list[tuple[str, str]] = []
    pending_role = ""
    index = 0
    while index < len(chunks):
        chunk = chunks[index].strip()
        index += 1
        if not chunk:
            continue
        if _is_role_chunk(chunk):
            if pairs and not pairs[-1][1]:
                pairs[-1] = (pairs[-1][0], chunk)
            else:
                pending_role = chunk
            continue
        name = _name_or_none(chunk, allow_single=False)
        if name is None:
            continue
        pairs.append((name, pending_role))
        pending_role = ""
    for name, role in pairs:
        _add(found, name, role, page, documents_path, line)


def _line_at(text: str, at: int) -> str:
    start = text.rfind("\n", 0, at) + 1
    end = text.find("\n", at)
    return " ".join(text[start : end if end != -1 else len(text)].split())


def _add(
    found: dict[str, Person],
    name: str,
    role: str,
    page: Any,
    documents_path: str,
    line: str,
) -> None:
    words = _WORD_RE.findall(name)
    if len(words) < 2 and not role:
        return
    key = _normal(name)
    if key in found:
        return
    source = f"{documents_path} {page.kind} p. {page.page}".strip()
    found[key] = Person(
        name=" ".join(words),
        first=" ".join(words[:-1]),
        last=words[-1],
        role=role,
        source=source,
        evidence=line[:240],
    )


def people_from_roster(roster: Any, path: str = "roster.json") -> list[Person]:
    """Every member a roster file lists, with the file and the member's line as the record."""
    lines: list[str] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    out = []
    for member in roster.members:
        quoted = next(
            (
                line.strip().rstrip(",")
                for line in lines
                if '"name"' in line and f'"{member.name}"' in line
            ),
            f'"name": "{member.name}", "role": "{member.role}"',
        )
        out.append(
            Person(
                name=member.name,
                first=member.first,
                last=member.last,
                role=member.role,
                source=path,
                evidence=quoted,
            )
        )
    return out


def load_people(
    rosters: list[tuple[Any, str]] | None = None,
    pages: dict[str, Any] | None = None,
    documents_path: str = "",
) -> People:
    """The people a meeting's records name, from its roster files and its documents."""
    persons: list[Person] = []
    seen: set[str] = set()
    for roster, path in rosters or []:
        for person in people_from_roster(roster, path):
            key = _normal(person.name)
            if key not in seen:
                seen.add(key)
                persons.append(person)
    if pages:
        for person in people_from_documents(pages, documents_path):
            key = _normal(person.name)
            if key not in seen:
                seen.add(key)
                persons.append(person)
    text = " ".join(
        [
            *(_normal(p.name) for p in persons),
            *(_normal(str(page.text)) for page in (pages or {}).values()),
        ]
    )
    return People(persons=tuple(persons), text=text)


def audit(people: People, texts: list[str]) -> list[str]:
    """One line per printed person name: the spelling, the record file, the record's words."""
    seen: set[str] = set()
    lines: list[str] = []
    for text in texts:
        for span in spans(text):
            person = people.match(span.name, span.role)
            if person is None:
                if _is_person_like(span.name, span.role) and not people.verbatim(span.name):
                    lines.append(f"- {span.name}: NO RECORD MATCH (must be a role)")
                continue
            tokens = _tokens(span.name)
            if len(tokens) == 1:
                exact = {
                    _normal(token)
                    for other in people.persons
                    for token in (*_tokens(other.first), *_tokens(other.last), *_tokens(other.name))
                }
                if _normal(span.name) not in exact:
                    continue
            elif people.verbatim(span.name) and tokens != _tokens(person.name):
                # A place or company the record prints ("Rock Creek"), not a person name.
                continue
            key = _normal(person.name)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f'- {person.name}: {person.source} — record: "{person.evidence}"')
    return lines
