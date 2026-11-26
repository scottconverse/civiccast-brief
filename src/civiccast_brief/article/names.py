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

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
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
    # Lafayette's title, and the captions' spellings of it ("Councelor Johnson").
    "Councilor",
    "Councillor",
    "Councelor",
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


#: The plural civic titles a story may print in front of a person's name. A closed list, so a
#: surname that merely ends in "s" ("Deans", "Towns", "Chiefs") is still a name, checked as a
#: person, and never skipped as if it were a title.
_PLURAL_TITLES = frozenset(
    {
        "members",
        "councilmembers",
        "councilors",
        "councillors",
        "trustees",
        "commissioners",
    }
)


def _singular_role_word(word: str) -> str:
    """A plural civic title as its singular, in the word's own case: "Members" -> "Member".

    Only the closed ``_PLURAL_TITLES`` list is singularised this way; any other word is
    returned unchanged, so a surname that ends in "s" is never read as a title.
    """
    return word[:-1] if word.lower() in _PLURAL_TITLES else word


def _is_role_word(word: str) -> bool:
    """Whether a word is a printed title or one of the closed plural titles ("Members")."""
    return word.lower() in _ROLE_WORD_SET or word.lower() in _PLURAL_TITLES


def is_plural_civic_title(word: str) -> bool:
    """Whether a word is a plural civic title ("Members", "Councilmembers", "Trustees").

    A plural title describes a group and gives no one a specific office, so other checks that
    treat a capitalised word as a name may skip it. A singular title ("Mayor", "Member") names
    an office and is never skipped.
    """
    return _is_plural_role_word(word)


def _is_singular_role_word(word: str) -> bool:
    """Whether a word is a singular printed title ("Member", "Trustee"), not a plural title."""
    return word.lower() in _ROLE_WORD_SET


def _is_plural_role_word(word: str) -> bool:
    return word.lower() in _PLURAL_TITLES


#: Lower-case joiners that may sit inside a printed title ("assistant superintendent of schools").
_TITLE_JOINERS = frozenset({"of", "for", "and", "the", "in", "on"})
#: The nouns a printed title ends with: the title is the words up to and including one of these.
_TITLE_NOUNS = frozenset(
    {
        "spokesperson",
        "spokeswoman",
        "spokesman",
        "officer",
        "reviewer",
        "director",
        "superintendent",
        "administrator",
        "manager",
        "clerk",
        "attorney",
        "supervisor",
        "coordinator",
        "specialist",
        "analyst",
        "engineer",
        "liaison",
        "principal",
        "trustee",
        "commissioner",
        "councilor",
        "councilmember",
        "mayor",
        "president",
        "secretary",
        "treasurer",
        "chief",
        "chair",
        "chairperson",
        "dean",
        "provost",
        "registrar",
    }
)
#: Words that make a comma phrase after a name a clause, not a title ("whose role is new").
_TITLE_STOPWORDS = frozenset(
    {
        "is",
        "was",
        "are",
        "were",
        "said",
        "says",
        "who",
        "whose",
        "that",
        "which",
        "role",
        "roles",
        "but",
        "with",
        "from",
        "after",
        "before",
        "when",
        "while",
        "has",
        "had",
        "have",
        "will",
        "would",
        "can",
        "could",
        "called",
        "named",
        "told",
        "asked",
        "described",
        "serves",
        "works",
    }
)
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")
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
    # A sentence starts with a capital whatever its first word is, and a subordinating
    # conjunction is never a person's name: "When Matthew Popkin asked ..." names Popkin, not
    # "When Matthew Popkin". The run of capitals joins them, so the opener is cut here.
    "when",
    "whenever",
    "while",
    "whilst",
    "if",
    "unless",
    "until",
    "till",
    "although",
    "though",
    "because",
    "since",
    "once",
    "as",
    "whereas",
    "whether",
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
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
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
#: Packet words that are never part of a person's name: articles, signature lines and the
#: department names a staff line prints after the person ("Joanne Zeas, Human Resources").
_PACKET_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "attest",
        "calendar",
        "human",
        "resources",
        "administration",
        "finance",
        "financial",
        "safety",
        "manager's",
        "clerk's",
        "attorney's",
    }
)
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
_SENTENCE_START = re.compile(r"(^|[.!?:][\"'”’)]?\s*)$")


@dataclass(frozen=True)
class Person:
    """A person the record names, with the record's spelling and where it is printed."""

    name: str
    first: str
    last: str
    role: str
    source: str
    evidence: str
    #: A member of another body's roster: matched only when printed exactly. A council
    #: transcript's "Michael Watson" is not the arts board's Rachel Hanson, and "Aurora Water"
    #: is not the youth board's Jordan Walker; only the meeting's own body and documents are
    #: close enough to correct a misheard spelling.
    exact_only: bool = False
    #: A member of another body's roster, even when this meeting's documents print the name
    #: (then misspellings are corrected, but a seat title still says it is not them).
    other_body: bool = False
    #: A member of the meeting's own body, from that body's roster file: the people a roll
    #: call names by surname alone ("Council Member Crist?").
    on_board: bool = False


@dataclass(frozen=True)
class People:
    """Every person the meeting's records name, and the record text to check against."""

    persons: tuple[Person, ...]
    text: str
    #: The normalised tokens of the city's own name ("Estes Park" -> {"estes", "park"}). A run
    #: that carries the city's name is a place, never an unmatched person ("South Golden").
    places: frozenset[str] = frozenset()

    def match(self, printed: str, role: str = "") -> Person | None:
        """The record person the printed name means, or None.

        ``role`` is the title printed in front of the name ("Mayor Pro Tem"), which identifies
        a member whose surname the audio mangled beyond spelling.
        """
        wanted = _tokens(printed)
        if not wanted:
            return None
        best: tuple[float, Person] | None = None
        printed_role = _role_key(role)
        for person in self.persons:
            # A title names the seat: "Councilor Johnson" is a councilor, not another board's
            # member who happens to be named Johnson.
            if person.other_body and printed_role and _role_key(person.role) not in printed_role:
                continue
            # The mayor is addressed as mayor: "Councilor Vegas" is a councilor, not Mayor
            # Tapia Vega.
            if _member_title(printed_role) and _role_key(person.role) == "mayor":
                continue
            score = _score(wanted, person, has_role=bool(role))
            if score and role and _role_key(role) == _role_key(person.role):
                score += 0.001  # break a tie between two similar names
            if score and (best is None or score > best[0]):
                best = (score, person)
        return best[1] if best and best[0] >= 0.62 else None

    def not_a_person(self, name: str) -> bool:
        """Whether a capitalised run is a place, plan, body or date, never a person.

        "Transit Village Area Plan", "Planning Board", "South Boulder Creek" and "Jan." name
        no one: the story must print them as written, not as a record name or as "a speaker".
        A run that reads as a record person's own name is still a person: "Jann Hill" is Jan
        Hill, but "University Hill" is a neighborhood.
        """
        tokens = _tokens(name)
        if not tokens:
            return False
        # The city's own name is a place, never an unmatched person ("Golden", "South Golden",
        # "Hunger Free Golden", "Golden Nazarene Church"). A run that exactly matches a record
        # person is still that person ("Pat Lyons" on a Lyons roster).
        if self.places and not any(_tokens(person.name) == tokens for person in self.persons):
            starts = [index for index, token in enumerate(tokens) if token in self.places]
            contiguous = bool(starts) and starts == list(range(starts[0], starts[0] + len(starts)))
            if (
                len(starts) == len(self.places)
                and contiguous
                and (
                    starts[0] == 0
                    or len(tokens) >= 3
                    or (len(tokens) == 2 and starts[0] == 1 and tokens[0] in _CITY_LEAD_WORDS)
                )
            ):
                return True
        if tokens[-1] not in _NON_PERSON_TAIL:
            return False
        for person in self.persons:
            last, first = _tokens(person.last), _tokens(person.first)
            if tokens[-1] not in last:
                continue
            if len(tokens) == 1:
                return False
            if max((_ratio(tokens[0], token) for token in first), default=0.0) >= 0.6:
                return False
        return True

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


#: Endings that make one word a place ("Oldtown", "Louisville", "Pittsburg"), not a surname.
_PLACE_SUFFIXES = ("town", "ville", "burg", "borough")


def _score(wanted: list[str], person: Person, has_role: bool = False) -> float:
    """How surely a printed name means this record person (0 = not at all).

    ``has_role`` is true when a title stands in front of the name ("Mayor Pro Tem Cowish"):
    the title already says a person is meant, so a weaker spelling may match on sound. With
    no title, an ordinary word ("Housing", "Senate Bill") must not become a member's name.
    """
    first, last = _tokens(person.first), _tokens(person.last)
    record = _tokens(person.name)
    printed = " ".join(wanted)
    full = _normal(person.name)
    if printed == full:
        return 1.0
    if person.exact_only:
        return 0.95 if len(wanted) == 1 and wanted[0] in (*first, *last) else 0.0
    # A surname alone cannot confirm a full name the record never prints in full.
    if len(wanted) >= 2 and len(record) == 1:
        return 0.0
    if len(wanted) == 1:
        word = wanted[0]
        if word in last or word in first or word in record:
            return 0.95
        # "Oldtown" is a neighborhood that sounds like Walton; a place word is never a
        # surname by sound.
        if word.endswith(_PLACE_SUFFIXES):
            return 0.0
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
        # "Housing" and "Sangay" share a sound key but are not the same word.
        if skeleton and best >= (0.4 if has_role else 0.6):
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
    # The run-together words must themselves spell the surname: "Bob Smithers" is not Joel
    # Smith, and "Transit Village Area Plan" is not Rob Kaplan.
    merged = _ratio("".join(wanted), "".join(last))
    if last_ratio >= 0.75 and first_ratio < 0.5 and merged >= 0.75:
        return max(score, 0.72)
    # With no title in front, a first word unlike the member's first name is not that member:
    # "Senate Bill" is not Angela Brill.
    if not has_role and first and first_ratio < 0.45:
        return 0.0
    # A surname that starts with another sound is a different person unless the first name
    # is the record's: "Jake Parsim" is Jake Marsing, but the commenter "John Bolton" is not
    # Jenn Ooton.
    if not has_role and last and _initial(wanted[-1]) != _initial(last[-1]) and first_ratio < 0.75:
        return 0.0
    return score


def _initial(word: str) -> str:
    """The sound class of a word's first letter ("c" and "k" are one; so are the vowels)."""
    letter = word[:1]
    return _skeleton(letter) or ("a" if letter in "aeiou" else letter)


def _role_key(role: str) -> str:
    # A roster adds the ward after a comma ("Mayor Pro Tem, Ward III"); the seat is before it.
    key = " ".join(_normal(role.split(",")[0]).replace("-", " ").split())
    # The captions write the seat three ways ("pro tem", "protem", "pro-tem").
    key = key.replace("protem", "pro tem")
    # And the councilor's title as the captions spell it.
    key = re.sub(r"\bcounc(?:e|il)l?or\b", "councilor", key)
    # A printed plural title is the same seat ("Council Members" -> "council member").
    return " ".join(_singular_role_word(word) for word in key.split())


_MEMBER_TITLE_RE = re.compile(r"\b(?:councilor|council member|councilmember|trustee)$")


def _member_title(role_key: str) -> bool:
    """Whether the printed title, nearest the name, is a member's: councilor or trustee."""
    return bool(_MEMBER_TITLE_RE.search(role_key))


def _holds_seat(printed_role: str, person: Person) -> bool:
    """Whether the title printed before a name is the seat this record person holds."""
    seat = _role_key(person.role)
    printed = _role_key(printed_role)
    return bool(seat and printed) and not person.other_body and printed.endswith(seat)


def _misheard_seat_holder(span: Span, person: Person) -> bool:
    """A misspelling of the record name under the seat's own title ("Councelor Johnson").

    A name already spelled as the record prints it needs no fix, so this is only a misspelling.
    """
    record = {*_tokens(person.first), *_tokens(person.last), *_tokens(person.name)}
    return _holds_seat(span.role, person) and not set(_tokens(span.name)) & record


def _role_phrase(role: str, context: str, at_start: bool) -> str:
    """A role to print instead of a name the record does not hold."""
    key = _normal(role)
    context_key = _normal(context)
    if "planning" in key or "building" in key:
        phrase = "a planning department staff member"
    elif "mayor" in key or "council" in key or "trustee" in key:
        phrase = "a council member"
    elif any(word in key for word in ("superintendent", "administrator")):
        phrase = "a district administrator"
    elif any(
        word in key
        for word in (
            "manager",
            "director",
            "clerk",
            "attorney",
            "chief",
            "supervisor",
            "staff",
            "officer",
            "reviewer",
            "coordinator",
            "principal",
            "analyst",
            "specialist",
            "engineer",
        )
    ):
        phrase = "a staff member"
    elif "public comment" in context_key or "resident" in context_key:
        phrase = "a resident"
    elif any(word in context_key for word in ("superintendent", "administrator")):
        # No title is printed in front, but the sentence says who the person is ("assistant
        # superintendent of schools").
        phrase = "a district administrator"
    elif any(
        word in context_key
        for word in (
            "staff",
            "director",
            "officer",
            "manager",
            "teacher",
            "principal",
        )
    ):
        # No title is printed in front, but the sentence says who the person is ("started
        # July 1 as the district's new director of gifted education").
        phrase = "a staff member"
    else:
        phrase = "a speaker"
    return phrase[:1].upper() + phrase[1:] if at_start else phrase


def _definite(role: str) -> str:
    """A role phrase for a later mention: "a staff member" -> "the staff member"."""
    lowered = role.lower()
    if lowered.startswith(("a ", "an ")):
        return "the " + role.split(" ", 1)[1]
    if lowered.startswith("the "):
        return role
    return "the " + role


def _cased(phrase: str, at_start: bool) -> str:
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


#: The last word of a company, partnership or firm: a run that ends in one of these names no
#: person, even beside a lead like "the applicant," (Boulder, Nov 2026: "Conscience Bay Company").
_COMPANY_WORDS = frozenset(
    {
        "company",
        "companies",
        "inc",
        "incorporated",
        "llc",
        "corp",
        "corporation",
        "partners",
        "partnership",
        "group",
        "holdings",
        "enterprises",
        "associates",
        "ventures",
        "industries",
        "ltd",
        "limited",
        "plc",
        "gmbh",
    }
)


#: A word that turns the city's name into a place: "South Golden", "Old Town", "Be Golden".
_CITY_LEAD_WORDS = frozenset(
    {
        "north",
        "south",
        "east",
        "west",
        "downtown",
        "old",
        "new",
        "greater",
        "historic",
        "central",
        "upper",
        "lower",
        "uptown",
        "be",
        "keep",
        "love",
        "visit",
    }
)


#: The last word of a plan, body, agency, district or place: a run that ends in one of these
#: names no one ("Boulder Valley Comprehensive Plan", "Federal Emergency Management Agency").
_NON_PERSON_TAIL = (
    _NOT_NAME_WORDS
    | _PLACE_WORDS
    | frozenset(
        {
            "plan",
            "plans",
            "agency",
            "authority",
            "administration",
            "association",
            "coalition",
            "foundation",
            "university",
            "college",
            "institute",
            "hospital",
            "airport",
            "creek",
            "lake",
            "river",
            "reservoir",
            "canyon",
            "mesa",
            "mountain",
            "valley",
            "hill",
            "hills",
            "village",
            "area",
            "areas",
            "land",
            "lands",
            "rural",
            "corridor",
            "zone",
            "zoning",
            "overlay",
            "map",
            "study",
            "project",
            "initiative",
            "policy",
            "strategy",
            "framework",
            "space",
            "open",
            "neighborhood",
            "subdivision",
            "campus",
            # Bills, events, programs, bodies and places a council story names (Longmont,
            # Nov 2026: "Senate Bill", "Dream Lab", "Colorado Gives Day", "Cities Summit").
            "bill",
            "bills",
            "act",
            "day",
            "days",
            "week",
            "month",
            "pass",
            "summit",
            "conference",
            "league",
            "lab",
            "labs",
            "communities",
            "community",
            "museum",
            "gallery",
            "library",
            "protectors",
            "north",
            "south",
            "east",
            "west",
        }
    )
)


def _is_person_like(name: str, role: str) -> bool:
    tokens = _tokens(name)
    if not tokens:
        return False
    # A printed title says a person is meant, even when the name ends in a company word:
    # "Mayor Ann Co" and "Council Member Jo Group" are people. A titleless run that ends in a
    # company word ("Conscience Bay Company") names no one.
    if role:
        return True
    if tokens[-1].casefold() in _COMPANY_WORDS:
        return False
    if tokens[-1] in _PLACE_WORDS:
        return False
    return len(tokens) >= 2


#: A speech or civic verb only a person does, right after a name: "Jo Smith said", "Jo Smith,
#: a resident, asked", "Jo Smith moved approval". A general action ("runs", "started", "came")
#: is not evidence: an event or a project does those too ("Winter Festival runs...", "Front
#: Range Rail came up..."). A run with no printed title and no person cue stays as written.
_PERSON_VERB_RE = re.compile(
    r"\s*(?:,[^,.;]{1,80},\s*)?(?:said|says|asked|asks|told|spoke|speaks|added|noted|moved|"
    r"seconded|explained|argued|urged|presented|reported|answered|replied|responded|agreed|"
    r"voted|testified|thanked|commented|questioned|recalled|warned|wrote)\b",
    re.IGNORECASE,
)
#: A word just before a name that says a person is meant: "resident Jo Smith", "named Jo Smith".
_PERSON_LEAD_RE = re.compile(
    r"\b(?:resident|speaker|named|commenter|applicant)\s*,?\s*$", re.IGNORECASE
)
#: The party words a name may be "of the ..." of ("Daniel Eisenman of the applicant"). A closed,
#: case-sensitive list: only a party to a land-use matter is a person's role. Any other "of the
#: <word>" is ordinary text ("Front Range of the state", "Winter Festival of the arts") and gives
#: no person cue at all, so it stays exactly as written.
_OF_ROLE_WORDS = ("applicant", "appellant", "petitioner", "developer")
_OF_ROLE_RE = re.compile(r"\s+of\s+the\s+(?P<role>" + "|".join(_OF_ROLE_WORDS) + r")\b")


def _of_role(text: str, span: Span) -> str:
    """The party role a printed name is "of the ..." of ("the applicant"), or ""."""
    if span.role:
        return ""
    tokens = _tokens(span.name)
    if not tokens or tokens[-1].casefold() in _NON_PERSON_TAIL | _COMPANY_WORDS:
        return ""
    match = _OF_ROLE_RE.match(text, span.end)
    if match is None:
        return ""
    return f"the {match.group('role')}"


#: A job named right after a run, which says a person is meant: "Jo Smith started July 1 as the
#: district's new director of gifted education". A project or an event has no job title, so
#: this is the one non-speech cue a titleless run may use.
_TITLE_NOUN_PATTERN = "|".join(sorted(_TITLE_NOUNS, key=len, reverse=True))
_PERSON_ROLE_AFTER_RE = re.compile(
    rf"(?:\s+[^\s.;!?]+){{1,6}}\s+as\s+(?:the|a|an)\s+"
    rf"(?:[^\s.;!?]+\s+){{0,4}}?(?i:{_TITLE_NOUN_PATTERN})\b"
)
#: The title (with its modifiers, determiner and any proper word inside it) a name is printed
#: under: "Chief Information Officer Frank Elmore", "the reviewer, Shelly Billig", "Title IX
#: compliance officer Liz Lindell". The title ends at a role noun, just before the name.
_LEAD_TITLE_RE = re.compile(
    r"(?P<lead>(?:(?:(?i:the|a|an|district'?s|city'?s|town'?s|deputy|assistant|associate|chief|"
    r"compliance|executive|senior|interim|acting|new|of|for|and)|[A-Z][A-Za-z'’\-]*|"
    r"[IVXLCDM]+)\s+){0,6}"
    r"(?i:spokesperson|spokeswoman|spokesman|officer|reviewer|director|superintendent|"
    r"administrator|manager|clerk|attorney|supervisor|coordinator|specialist|analyst|"
    r"engineer|liaison|principal|trustee|commissioner|councilor|councilmember|"
    r"council\s+member|mayor|president|secretary|treasurer|chief))"
    r"(?P<comma>\s*,)?\s*$"
)
#: A title printed after the name as an appositive: "Neil Anderson, assistant superintendent of
#: schools, said".
_APPOSITIVE_TITLE_RE = re.compile(r"\s*,\s*(?P<title>[^,.;:]{1,80}?)\s*,")


def _title_before(text: str, span: Span) -> tuple[int, str, bool] | None:
    """The title printed just before a name: (start, title words, followed by a comma)."""
    match = _LEAD_TITLE_RE.search(text[: span.start])
    if match is None:
        return None
    title = " ".join(match.group("lead").split())
    if not any(word.lower() in _TITLE_NOUNS for word in title.split()):
        return None
    return match.start("lead"), title, bool(match.group("comma"))


def _title_after(text: str, span: Span) -> tuple[str, int] | None:
    """The title printed after a name as an appositive: (title, where it ends).

    Only a noun phrase with a title noun counts. A clause ("raising class time as a possible
    factor", "whose role is new") is not a title and is left in place.
    """
    match = _APPOSITIVE_TITLE_RE.match(text, span.end)
    if match is None:
        return None
    title = " ".join(match.group("title").split())
    words = _normal(title).split()
    if not words or len(words) > 5:
        return None
    if any(word in _TITLE_STOPWORDS for word in words):
        return None
    if not any(word in _TITLE_NOUNS for word in words):
        return None
    return title, match.end()


def _printed_title(text: str, span: Span) -> str | None:
    """The title a name is printed under, in front or after it, or None."""
    if span.role:
        return span.role
    before = _title_before(text, span)
    if before is not None:
        return before[1]
    after = _title_after(text, span)
    return after[0] if after is not None else None


def _title_key(title: str) -> str:
    """A title's words without its determiner, for comparing two printings of one title."""
    return " ".join(word for word in _normal(title).split() if word not in {"the", "a", "an"})


def _label_from_title(title: str) -> str:
    """The definite label a printed title becomes: "Deputy Superintendent" -> "the deputy
    superintendent"; "Title IX compliance officer" keeps its proper words."""
    words = title.split()
    while words and words[0].lower() in {"the", "a", "an"}:
        words = words[1:]
    if not words:
        return "the staff member"
    out: list[str] = []
    for index, word in enumerate(words):
        # A plural title printed before one person labels that person in the singular:
        # "Council Members X" -> "the council member".
        word = _singular_role_word(word)
        proper = _ROMAN_RE.match(word) or (len(word) >= 2 and word.isupper())
        if word == "Title":
            following = words[index + 1] if index + 1 < len(words) else ""
            out.append("Title" if _ROMAN_RE.match(following) else "title")
        elif proper:
            out.append(word)
        else:
            out.append(word.lower())
    return "the " + " ".join(out)


def _more_specific(current: str | None, candidate: str) -> str:
    """The more specific of two printed titles: more words wins, never a shortened job."""
    if current is None:
        return candidate
    if (len(candidate.split()), len(candidate)) > (len(current.split()), len(current)):
        return candidate
    return current


def _indefinite(label: str) -> str:
    """The first-mention form of a seat label: "the commissioner" -> "a commissioner"."""
    head, _, rest = label.partition(" ")
    if head.lower() == "the" and rest:
        return f"a {rest}"
    return label


#: The last word of a seat that several members of one body hold. Such a seat is not a unique
#: job: the first mention is "a commissioner", not "the commissioner", and a later mention of
#: the same person, or of a second holder, is told apart as "the commissioner" / "the second
#: commissioner". Unique offices ("deputy superintendent", "chief information officer") keep
#: "the" on every mention.
_SEAT_TAILS = frozenset(
    {
        "commissioner",
        "commissioner's",
        "trustee",
        "representative",
        "councilor",
        "councillor",
        "councelor",
        "councilmember",
    }
)


def _is_seat_title(title: str) -> bool:
    """Whether a printed title names a seat more than one member of the body can hold."""
    words = [_singular_role_word(word) for word in _normal(title).split()]
    if not words:
        return False
    if words[-1] in _SEAT_TAILS:
        return True
    joined = f" {' '.join(words)} "
    if " council member " in joined or " board member " in joined:
        return True
    if " commission member " in joined:
        return True
    # "Director of the board" is a seat of that board; a "director of gifted education" is a
    # unique staff job and keeps "the".
    return "director" in words and ("board" in words or "commission" in words)


def _is_person_title(role: str) -> bool:
    """Whether a title printed in front of a run can introduce a person's name."""
    key = _role_key(role)
    words = key.split()
    if not words:
        return False
    return words[-1] in _TITLE_NOUNS or _is_seat_title(role) or key.endswith("pro tem")


def _with_second(phrase: str) -> str:
    """ "the district administrator" -> "the second district administrator"."""
    head, _, rest = phrase.partition(" ")
    if head.lower() in {"the", "a", "an"} and rest:
        return f"{head} second {rest}"
    return f"a second {phrase}"


#: Position words for the third and later holders of one seat in one text.
_SEAT_ORDINALS = (
    "another",
    "a third",
    "a fourth",
    "a fifth",
    "a sixth",
    "a seventh",
    "an eighth",
    "a ninth",
    "a tenth",
)


def _seat_label(base: str, index: int) -> str:
    """The label for the holder at ``index`` (0 = first) of one seat within one text.

    "a commissioner", then "another commissioner", then "a third commissioner". A holder is
    never "the commissioner" while another holder could be meant by it.
    """
    head, _, rest = base.partition(" ")
    tail = rest if head.lower() in {"a", "an"} and rest else base
    if index == 0:
        return base
    if index - 1 < len(_SEAT_ORDINALS) - 1:
        word = _SEAT_ORDINALS[index - 1]
        return f"another {tail}" if word == "another" else f"{word} {tail}"
    return f"a {index + 1}th {tail}"


def _takes_label(text: str, span: Span, people: People, story: _Story) -> bool:
    """Whether this span loses its name to the label the story keeps for its person."""
    if not span.role and people.not_a_person(span.name):
        return False
    if people.match(span.name, span.role) is not None:
        return False
    if people.verbatim(span.name):
        return False
    tokens = _tokens(span.name)
    if not tokens:
        return False
    surname = tokens[-1]
    known_here = surname in story.labels and (
        len(tokens) == 1 or _is_person_like(span.name, span.role)
    )
    return (known_here or _unmatched_person(text, span)) and surname in story.labels


def _text_seat_labels(
    text: str, people: People, story: _Story, quoted: list[tuple[int, int]]
) -> dict[str, dict[str, str]]:
    """Each shared seat's holders in one text, in print order, with their labels.

    The first holder is "a commissioner", a different holder "another commissioner", a third
    "a third commissioner". The map is built for the whole text before any replacement so a
    later mention knows whether another holder shares the text and "the" would be ambiguous.
    """
    state: dict[str, dict[str, str]] = {}
    if not story.seat_multi:
        return state
    for span in spans(text):
        if _inside(quoted, span.start) or not _takes_label(text, span, people, story):
            continue
        surname = _tokens(span.name)[-1]
        group = story.seat_multi.get(surname)
        if group is None:
            continue
        holders = state.setdefault(group, {})
        if surname not in holders:
            holders[surname] = _seat_label(story.seat_base[surname], len(holders))
    return state


def _unmatched_person(text: str, span: Span) -> bool:
    """Whether a name the record does not hold is a person's, so it must lose the name.

    With a title in front ("Council Member X") it is. With none, a run of capitalised words is
    more often a bill, a program, a body or a place ("Senate Bill", "Dream Lab", "Thistle
    Resident-Owned Communities"); only a person cue around it (a speech verb after, a job named
    after it, "resident" before) makes it a person. A general action verb is not enough: an
    event runs and a rail project comes up. Otherwise the words stay as written.
    """
    if not _is_person_like(span.name, span.role):
        return False
    if span.role:
        return True
    if _title_before(text, span) is not None:
        return True
    return bool(
        _PERSON_VERB_RE.match(text, span.end)
        or _PERSON_ROLE_AFTER_RE.match(text, span.end)
        or _of_role(text, span)
        or _PERSON_LEAD_RE.search(text[max(0, span.lead_start - 40) : span.lead_start])
    )


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
        raw = match.group(0)
        # A run that ends in a possessive keeps the name only.
        if raw.endswith(("'s", "’s")):
            raw = raw[:-2]
        words = [(word.group(0), word.start(), word.end()) for word in _WORD_RE.finditer(raw)]
        if not words:
            continue
        index = 0
        while index < len(words) and words[index][0].lower() in _OPENERS:
            index += 1
        role_words: list[str] = []
        while index < len(words) and _is_role_word(words[index][0]):
            role_words.append(words[index][0])
            index += 1
        # A capitalised word inside a printed title is still part of it: "Chief Information
        # Officer Frank Elmore" is the title "Chief Information Officer" and the name
        # "Frank Elmore". The title keeps running while a later role word closes it.
        while role_words:
            stop = index
            while stop < len(words) and not _is_role_word(words[stop][0]):
                stop += 1
            if (
                stop < len(words)
                and index < stop <= index + 3
                and stop + 1 < len(words)
                and words[index][0][:1].isupper()
                and all(
                    word[0][:1].isupper() or word[0].lower() in _TITLE_JOINERS
                    for word in words[index:stop]
                )
            ):
                role_words.extend(word[0] for word in words[index : stop + 1])
                index = stop + 1
            else:
                break
        kept = words[index:]
        if not kept:
            continue
        # A singular role word left in the name is a heading, not a person ("Board Member
        # Davis"), and a name with no title in front must be one contiguous run. A plural title
        # is a title only in the run before a name; inside the name it is never skipped, so a
        # surname that looks plural ("Deans", "Towns") is still checked as a person.
        if any(_is_singular_role_word(word[0]) for word in kept):
            continue
        tokens = [word for word, _start, _end in kept]
        role = " ".join(role_words)
        # A plural title only introduces a person when a name follows it. Otherwise it is a
        # body's name: "Northwest Mayors and Commissioners Coalition" names no one.
        if (
            any(_is_plural_role_word(word) for word in role_words)
            and len(tokens) == 1
            and tokens[0].lower() in _NON_PERSON_TAIL
        ):
            continue
        # "Ordinance O-2026-59" is a document number, not a person.
        if any(len(t.strip("'’\\-")) <= 1 for t in tokens):
            continue
        # A word that is a month, place, body or heading is not a name on its own, unless a
        # person's title stands in front: "Commissioner Mar" names Mar, while "Town Hall" is
        # still the hall. ("Mar" is also the month abbreviation in _NOT_PERSON.)
        if all(t.lower() in _NOT_NAME_WORDS for t in tokens) and not (
            role and _is_person_title(role)
        ):
            continue
        lowered = {token.lower() for token in tokens}
        if tokens[0].lower() in _DOCUMENT_WORDS or lowered & _DOCUMENT_STRONG:
            continue
        # "Full-depth" is a compound word, not a person: a surname's second half is capitalised.
        if re.search(r"-[a-z]", " ".join(tokens)):
            continue
        found.append(
            Span(
                match.start() + kept[0][1],
                match.start() + kept[-1][2],
                " ".join(tokens),
                role,
                match.start() + words[0][1],
            )
        )
    return found


def _at_sentence_start(text: str, start: int) -> bool:
    return bool(_SENTENCE_START.search(text[:start]))


def _starts_like(word: str, person: Person) -> bool:
    """Whether a name's first word sounds like the person's first name or a surname part."""
    first = [t for t in _tokens(person.first) if t]
    last = [t for t in _tokens(person.last) if t]
    if any(_ratio(word, token) >= 0.6 for token in first):
        return True
    return any(word == token or _ratio(word, token) >= 0.6 for token in last)


def ordinary_words(texts: Iterable[str]) -> set[str]:
    """The words these texts print in lower case somewhere: ordinary words, not names."""
    return {
        match.group(0)
        for text in texts
        for match in re.finditer(r"(?<![\w'])[a-z][a-z']*(?![\w'])", text)
    }


def _sentence_word(text: str, span: Span, person: Person) -> bool:
    """A lone capitalised word at a sentence start that only sounds like a record name.

    Every first word of a sentence is capitalised, so "Sitting also as the authority ..." is
    not the surname "Otting". Such a word is changed only when it is the person's own name.
    """
    tokens = _tokens(span.name)
    if len(tokens) != 1 or span.role or not _at_sentence_start(text, span.lead_start):
        return False
    return tokens[0] not in {*_tokens(person.last), *_tokens(person.first)}


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
        if not span.role and people.not_a_person(span.name):
            continue
        person = people.match(span.name, span.role)
        if person is None:
            if _unmatched_person(text, span) and not people.verbatim(span.name):
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
        if not span.role and people.not_a_person(span.name):
            continue
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


#: How close a printed full name's surname must be to the record's for the site to print the
#: record's spelling. "Parsim" -> "Marsing" (0.62) and "Buckspan" -> "Buchsbaum" (0.59) are the
#: same people; "Bolton" -> "Lennon" (0.50) is not.
_TWO_WORD_SURNAME = 0.55


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
        if not span.role and people.not_a_person(span.name):
            continue
        person = people.match(span.name, span.role)
        # A word the record prints is left alone, unless the printed title names the seat the
        # record person holds: the captions' "Councelor Johnson" is Councilor Jensen even though
        # a commenter named Johnson is in the packet.
        if person is None:
            continue
        if people.verbatim(span.name):
            if _misheard_seat_holder(span, person):
                # The pair keeps the title, so the fix applies only under it: the same text's
                # "Next we'll hear from Roy Johnson" is the commenter and stays as printed.
                printed = text[span.lead_start : span.end]
                out.append((printed, text[span.lead_start : span.start] + person.name))
            continue
        if _tokens(span.name) == _tokens(person.name) or _sentence_word(text, span, person):
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
        elif max((_ratio(tokens[-1], token) for token in last), default=0.0) < _TWO_WORD_SURNAME:
            continue
        elif not _starts_like(tokens[0], person) and (
            # "Craig Craft" is one surname heard as two words: Cracraft.
            _ratio("".join(tokens), "".join(last)) < 0.75
            or "".join(tokens)[:2] != "".join(last)[:2]
        ):
            # Two words are a name only when the first sounds like the person's first name or
            # part of the surname: "Lake Powell" is a lake, not Greg Lowell, and "Mayor Prom
            # Miller" is the pro tem, not a Mayor Luke.
            continue
        if len(tokens) == 1 and span.role:
            # The pair keeps the title, so the site changes the name only under it:
            # "Commissioner Bear" is Commissioner Baer, but a bear elsewhere is a bear.
            lead = text[span.lead_start : span.start]
            out.append((lead + span.name, lead + _spelling(span, person)))
            continue
        out.append((span.name, _spelling(span, person)))
    return out


@dataclass
class _Story:
    """The label each replaced person prints under, shared by the whole story.

    ``labels`` is the definite form a later mention takes ("the deputy superintendent");
    ``first`` is the first mention's form, indefinite for a person who has no printed title
    ("a district administrator"). ``seen`` records who has already been mentioned.

    ``seat_multi`` maps a surname to the shared seat it holds when two or more replaced people
    in the story hold that same seat ("commissioner"); such a seat is labelled within each
    text, never by ``labels``/``first``, so no "the commissioner" can point at the wrong
    holder. ``seat_base`` is that seat's indefinite form ("a commissioner").
    """

    labels: dict[str, str]
    first: dict[str, str]
    seen: set[str]
    seat_multi: dict[str, str] = field(default_factory=dict)
    seat_base: dict[str, str] = field(default_factory=dict)


def _quoted_ranges(text: str) -> list[tuple[int, int]]:
    """Where each quotation in a text starts and ends; a name inside one is never changed."""
    return [(match.start(), match.end()) for match in re.finditer(r'"[^"]*"|“[^”]*”', text)]


def _inside(ranges: list[tuple[int, int]], at: int) -> bool:
    return any(start < at < end for start, end in ranges)


def _replaceable(text: str, span: Span, people: People) -> bool:
    """Whether this span is a person's name the record does not hold, so it loses the name."""
    if not span.role and people.not_a_person(span.name):
        return False
    if people.match(span.name, span.role) is not None:
        return False
    if people.verbatim(span.name):
        return False
    return _unmatched_person(text, span)


def _story_labels(texts: Iterable[str], people: People) -> _Story:
    """Find every printed title before replacing, and give each person one label.

    A person's most specific printed title becomes their label; a person with no title keeps a
    plain role. Two people who would share a label are told apart as "a second ...".
    """
    titles: dict[str, str] = {}
    plain: dict[str, str] = {}
    order: list[str] = []
    for text in texts:
        if not text:
            continue
        quoted = _quoted_ranges(text)
        for span in spans(text):
            if not _replaceable(text, span, people):
                continue
            if _inside(quoted, span.start):
                continue
            tokens = _tokens(span.name)
            if not tokens:
                continue
            surname = tokens[-1]
            if surname not in order:
                order.append(surname)
            title = _printed_title(text, span)
            if title:
                titles[surname] = _more_specific(titles.get(surname), title)
            elif of_role := _of_role(text, span):
                # "Daniel Eisenman of the applicant" prints as "the applicant": the role is the
                # label, and a later bare "Eisenman" takes the same one.
                plain.setdefault(surname, of_role)
            elif surname not in plain:
                context = text[max(0, span.start - 60) : span.end + 60]
                plain[surname] = _role_phrase("", context, False)
    labels: dict[str, str] = {}
    first: dict[str, str] = {}
    seat_base: dict[str, str] = {}
    for surname in order:
        title = titles.get(surname)
        if title:
            label = _label_from_title(title)
            labels[surname] = label
            # A seat several members hold is indefinite on its first mention ("a
            # commissioner"); a unique office keeps "the" ("the deputy superintendent").
            first[surname] = _indefinite(label) if _is_seat_title(title) else label
            if _is_seat_title(title):
                seat_base[surname] = first[surname]
            continue
        base = plain.get(surname, "a speaker")
        lowered = base.lower()
        if lowered.startswith(("a ", "an ")):
            first[surname], labels[surname] = base, _definite(base)
        elif lowered.startswith("the "):
            first[surname] = labels[surname] = base
        else:
            first[surname], labels[surname] = "a " + base, "the " + base
    # Two different people never share a label: the later one is "a second ...". A seat that
    # two or more replaced people hold is the exception: it is labelled within each text
    # ("a commissioner", "another commissioner"), so no "the commissioner" can mean one of
    # them, and neither takes a story-wide "second".
    groups: dict[str, list[str]] = {}
    for surname in order:
        groups.setdefault(_normal(labels[surname]), []).append(surname)
    seat_multi: dict[str, str] = {}
    for group in groups.values():
        if len(group) >= 2 and all(surname in seat_base for surname in group):
            for surname in group:
                seat_multi[surname] = _normal(seat_base[group[0]])
            continue
        for surname in group[1:]:
            labels[surname] = _with_second(labels[surname])
            first[surname] = _with_second(first[surname])
    return _Story(labels, first, set(), seat_multi, seat_base)


def _titles_match(title: str, label: str) -> bool:
    left, right = _title_key(title), _title_key(label)
    return bool(left and right) and (left == right or left in right or right in left)


def _label_range(text: str, span: Span, label: str) -> tuple[int, int]:
    """The character range a label replaces: the title and name, an appositive, or the name."""
    if span.role:
        start = span.lead_start
        # A determiner printed in front of the title goes with it, so "the the" cannot happen.
        determiner = re.search(r"\b(?:the|a|an)\s+$", text[:start], re.IGNORECASE)
        if determiner is not None:
            start = determiner.start()
        return start, span.end
    before = _title_before(text, span)
    if before is not None:
        start, _title, comma = before
        end = span.end
        if comma:
            # The comma before the name is inside the range; the comma after it goes too, but
            # the space that follows stays so the next word is not run into the label.
            tail = re.match(r"\s*,", text[end:])
            if tail is not None:
                end += tail.end()
        return start, end
    after = _title_after(text, span)
    if after is not None and _titles_match(after[0], label):
        # The same title is printed after the name ("Neil Anderson, assistant superintendent of
        # schools, said"): the label stands in for the whole phrase, commas and all.
        return span.start, after[1]
    of_role = _of_role(text, span)
    # "Daniel Eisenman of the applicant" -> "The applicant": the "of the applicant" is the
    # printed role, so the label takes its place rather than reading "a speaker of the ...".
    if (
        of_role
        and _tokens(of_role)[-1:] == _tokens(label)[-1:]
        and (match := _OF_ROLE_RE.match(text, span.end)) is not None
    ):
        return span.start, match.end()
    return span.start, span.end


def repair_text(
    text: str, people: People | None, story: _Story | None = None
) -> tuple[str, list[str]]:
    """Replace each record-matched name with the record's spelling, and each unmatched name
    with the one label its person keeps across the whole story.

    An empty record (``People(persons=())``) still replaces a person's name: the rule is that a
    name prints only when a record holds it, and an empty record holds none.

    ``story`` carries every replaced person's label between the parts of one story, so a later
    paragraph's bare "Anderson said" takes the title the full name was printed under, and two
    people never share a label. A name inside quotation marks is left as printed and reported.
    """
    if people is None:
        return text, []
    if story is None:
        story = _story_labels([text], people)
    changes: list[str] = []
    quoted = _quoted_ranges(text)
    seat_state = _text_seat_labels(text, people, story, quoted)
    seat_used: set[str] = set()
    edits: list[tuple[int, int, str, str]] = []
    for span in spans(text):
        if not span.role and people.not_a_person(span.name):
            continue
        person = people.match(span.name, span.role)
        if person is not None:
            if _tokens(span.name) == _tokens(person.name) or people.verbatim(span.name):
                continue
            if _sentence_word(text, span, person):
                continue
            spelling = _spelling(span, person)
            edits.append(
                (
                    span.start,
                    span.end,
                    spelling,
                    f"name {span.name!r} -> {spelling!r} ({person.source})",
                )
            )
            continue
        if people.verbatim(span.name):
            continue
        tokens = _tokens(span.name)
        if not tokens:
            continue
        surname = tokens[-1]
        if _inside(quoted, span.start):
            # A quotation's words are the speaker's, not the story's: report, never rewrite.
            if len(tokens) >= 2 or span.role or surname in story.labels:
                changes.append(f"name {span.name!r} is inside a quotation; left as printed")
            continue
        if not _takes_label(text, span, people, story):
            continue
        group = story.seat_multi.get(surname)
        if group is not None:
            # A seat two or more replaced people hold is labelled within this text: the first
            # holder is "a commissioner", a different one "another commissioner". A holder is
            # "the commissioner" only when no other holder shares the text.
            holders = seat_state[group]
            if surname in seat_used:
                label = _definite(holders[surname]) if len(holders) == 1 else holders[surname]
            else:
                label = holders[surname]
                seat_used.add(surname)
        else:
            first = surname not in story.seen
            story.seen.add(surname)
            label = story.first[surname] if first else story.labels[surname]
        start, end = _label_range(text, span, label)
        replacement = _cased(label, _at_sentence_start(text, start))
        edits.append(
            (
                start,
                end,
                replacement,
                f"name {span.name!r} -> {replacement!r} (no record match)",
            )
        )
    out = text
    for start, end, replacement, message in sorted(edits, key=lambda edit: -edit[0]):
        out = out[:start] + replacement + out[end:]
        changes.append(message)
    return out, changes


def repair_article(
    article: Any, people: People | None, *, add_seat_names: bool = True
) -> tuple[Any, list[str]]:
    """Repair every printed name in a story, in place of :class:`Article` (same shape).

    ``add_seat_names`` names a seat the story printed without a person ("Mayor Pro Tem" ->
    "Mayor Pro Tem Jenn Kaaoush") from the roster. A roster can be stale, so the skill story
    path sets it false: a seat-only mention stays as written, and repair never adds a name the
    sentence did not have.
    """
    if people is None:
        return article, []
    changes: list[str] = []
    texts = [article.headline, article.dek or ""]
    for section in article.sections:
        if section.heading:
            texts.append(section.heading)
        texts.extend(paragraph.text for paragraph in section.paragraphs)
    for vote in article.votes:
        texts.extend((vote.motion, vote.result))
        if vote.tally:
            texts.append(vote.tally)
    texts.extend(quote.speaker for quote in article.quotes)
    # One label per person, decided from every title the whole story prints.
    story = _story_labels(texts, people)

    def fix(text: str) -> str:
        new, lines = repair_text(text, people, story)
        changes.extend(lines)
        if add_seat_names:
            new, seat_lines = fill_roll_call_names(new, people)
            changes.extend(seat_lines)
        return new

    # Repair in story order, so the first mention of a person takes the first-mention form.
    headline = fix(article.headline)
    dek = fix(article.dek) if article.dek is not None else None
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
            headline=headline,
            dek=dek,
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
    # After a comma, a clause connector is not a name: ", and council members" ends the tail.
    r"((?:\s*,\s*(?!(?:and|or|but)\b)[A-Za-z][A-Za-z'’\-]*"
    r"|\s+[A-Za-z][A-Za-z'’\-]*){0,2})",
    re.IGNORECASE,
)
#: A name just before the seat ("Tara Winer, the mayor pro tem"). The name is printed once.
_NAME_BEFORE_SEAT_RE = re.compile(
    r"([A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*)?)\s*,?\s*(?:the\s+)?$"
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


#: A spoken filler between the seat and the holder's name ("Mayor Pro Tem the Cowish").
_FILLER_AFTER_SEAT = frozenset({"the", "uh", "um"})


def _seat_holder(people: People, role: str) -> Person | None:
    """The record person who holds a seat, preferring a printed first and last name.

    The packet sometimes prints only a surname ("Mayor Pro-tem Kaaoush") beside the roster's
    full name ("Jenn Kaaoush"). The full name is the one to print.
    """
    holders = [p for p in people.persons if _role_key(p.role) == _role_key(role)]
    # The roster says who holds the seat now. A packet can print an earlier holder ("2024 -
    # Mayor Pro Tem Dickinson" in a committee's history), so the roster wins when it names one.
    current = [p for p in holders if not p.other_body and p.source.endswith(".json")]
    holders = current or holders
    if not holders:
        return None
    return max(holders, key=lambda p: (len(_tokens(p.name)), p.name))


def _names_holder_before(text: str, start: int, person: Person) -> bool:
    """Whether the words just before the seat already name its holder.

    "Tara Winer, the mayor pro tem" prints the name once; naming the seat again would print
    "Tara Winer, the Mayor Pro Tem Tara Winer". Only the holder's own printed name counts.
    """
    match = _NAME_BEFORE_SEAT_RE.search(text[:start])
    if match is None:
        return False
    before = set(_tokens(match.group(1)))
    return bool(before) and before <= set(_tokens(person.name))


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
    words = list(_WORD_RE.finditer(tail))

    # Only the holder's printed name, or a misheard spelling of it, is replaced. Every other
    # word after the seat stays as printed, commas too: "mayor pro tem, city manager" is two
    # people, and "pro tem or whoever's" keeps its "or".
    def holder(text: str) -> bool:
        # A misheard spelling of the holder ("mayor protem cowish", "Jen Kawish"): a longer
        # word, or a capitalised one, so an ordinary "time" is not taken for "Tim".
        return (len(text) >= 5 or text[:1].isupper()) and _score(
            _tokens(text), person, has_role=True
        ) > 0

    consumed = 0
    # "Mayor Pro Tem the Cowish": a filler word before the holder's misheard name goes too.
    if len(words) > 1 and words[0].group(0).lower() in _FILLER_AFTER_SEAT:
        if not holder(words[1].group(0)):
            return record + tail
        consumed = words[0].end()
        words = words[1:]
    for word in words[:2]:
        text = word.group(0)
        if text.lower() in _NOT_A_NAME_AFTER_SEAT:
            break
        exact = _normal(text) in (*_tokens(person.first), *_tokens(person.last))
        if (
            not exact
            and consumed == 0
            and people is not None
            and _other_member(text, person, people)
        ):
            # Another member is printed after the seat ("Mayor Pro Tem, Stephanie Miller").
            # The seat takes the record's holder; the other keeps their name.
            rest = tail if tail.lstrip().startswith(",") else "," + tail
            return record + rest
        if not (exact or holder(text)):
            break
        consumed = word.end()
    return record + tail[consumed:]


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
        if _names_holder_before(text, match.start(), person):
            continue
        tail = match.group(1) or ""
        replacement = _seat_replacement(tail, person, people)
        if replacement != match.group(0):
            out.append((match.group(0), replacement))
    return out


def seat_name_fixes(text: str, people: People | None) -> list[tuple[str, str]]:
    """The seat pairs that correct a printed name of the mayor pro tem, for the site.

    The site replaces each pair wherever it is printed, transcript included, so a pair that
    only adds the holder's name to a bare title is left out: "electing a new mayor protem" is
    not Brandon Bell, and a transcript keeps the words that were said.
    """
    person = _seat_holder(people, "Mayor Pro Tem") if people is not None else None
    if person is None:
        return []
    out: list[tuple[str, str]] = []
    for wrong, right in seat_fixes(text, people):
        match = _MAYOR_PRO_TEM_RE.fullmatch(wrong)
        if match is not None and right == f"Mayor Pro Tem {person.name}{match.group(1) or ''}":
            continue
        out.append((wrong, right))
    return out


def apply_fixes(text: str, pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    """Replace each printed name with its record spelling, longest form first.

    A pair whose record spelling starts with the printed form ("Mayor Pro Tem" ->
    "Mayor Pro Tem Jenn Kaaoush") is not applied where that name is already printed, so one
    replacement cannot repeat the name.
    """
    for wrong, right in sorted(pairs, key=lambda pair: -len(pair[0])):
        remainder = right[len(wrong) :] if right.startswith(wrong) else ""
        # Whole words only: "Shaw" is not the start of "Shawnee".
        pattern = r"(?<![\w'])" + re.escape(wrong) + r"(?![\w'])"
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
            if _names_holder_before(match.string, match.start(), person):
                return match.group(0)
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
    r"(?:moved|seconded|motion|made)\s+by[ \t]+"
    r"([A-Z][A-Za-z'’\-]+(?:[ \t]+[A-Z][A-Za-z'’\-]+){0,2})",
    re.IGNORECASE,
)
_ROLE_BEFORE_RE = re.compile(
    r"(Mayor\s+Pro[- ]?[Tt]em|Mayor|Council\s*Member|Council\s+President|Trustee|Commissioner|"
    r"Town\s+Manager|Assistant\s+Town\s+Manager|Asst\.?\s+Town\s+Manager|Deputy\s+Town\s+Clerk|"
    r"Town\s+Clerk|Town\s+Attorney|Director|Chief|Superintendent|Administrator|Supervisor|"
    r"Attorney|Clerk)[ \t]+([A-Z][A-Za-z'’\-]+(?:[ \t]+[A-Z][A-Za-z'’\-]+){0,2})"
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
    # One heading or role word makes the run a heading, not a person: "Council Calendar City",
    # "Board Member Davis", "Financial Officer", "Human Resources", and "The" before "City
    # Clerk" are packet text. Every word was required before, so those became people and the
    # site swapped real phrases for them.
    if not words or any(
        word.lower() in _NOT_NAME_WORDS
        or word.lower() in _ROLE_WORD_SET
        or word.lower() in _PACKET_WORDS
        for word in words
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


def city_name_of(folder: Path | None) -> str:
    """The city's own name from ``city.json`` in its folder, or "" when it cannot be read."""
    if folder is None:
        return ""
    try:
        data = json.loads((folder / "city.json").read_text(encoding="utf-8"))
        name = data["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return name if isinstance(name, str) else ""


def load_people(
    rosters: list[tuple[Any, str]] | None = None,
    pages: dict[str, Any] | None = None,
    documents_path: str = "",
    other_rosters: list[tuple[Any, str]] | None = None,
    city_name: str = "",
) -> People:
    """The people a meeting's records name, from its roster files and its documents.

    ``other_rosters`` are the city's other bodies. Their members are known by name, so a name
    printed exactly is theirs, but a misheard spelling is never corrected to one of them: a
    council meeting's speaker is not a member of the arts board who sounds alike.
    """
    persons: list[Person] = []
    seen: set[str] = set()

    def keep(person: Person) -> None:
        key = _normal(person.name)
        if key not in seen:
            seen.add(key)
            persons.append(person)

    for roster, path in rosters or []:
        for person in people_from_roster(roster, path):
            keep(replace(person, on_board=True))
    if pages:
        for person in people_from_documents(pages, documents_path):
            keep(person)
    printed = f" {_normal(' '.join(str(page.text) for page in (pages or {}).values()))} "
    for roster, path in other_rosters or []:
        for person in people_from_roster(roster, path):
            # The meeting's own documents print this member's full name ("Mayoral Appointments:
            # Neal Evers to Planning & Community Development"), so they are part of this meeting
            # and a misheard spelling of them is corrected like any other.
            here = f" {_normal(person.name)} " in printed
            keep(replace(person, other_body=True, exact_only=not here))
    text = " ".join(
        [
            *(_normal(p.name) for p in persons),
            *(_normal(str(page.text)) for page in (pages or {}).values()),
        ]
    )
    return People(persons=tuple(persons), text=text, places=frozenset(_tokens(city_name)))


def audit(people: People, texts: list[str]) -> list[str]:
    """One line per printed person name: the spelling, the record file, the record's words."""
    seen: set[str] = set()
    lines: list[str] = []
    for text in texts:
        for span in spans(text):
            if not span.role and people.not_a_person(span.name):
                continue
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
