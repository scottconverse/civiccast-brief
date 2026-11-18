"""The meeting documents' own terms: resolution and ordinance numbers, item and company names.

A story may print a resolution number or an item name only as the meeting's documents print it.
The captions are speech-to-text: they hear "R-991" for "R-91" and "Nouvel wind swings" for
"Noblewins Swings". The check here reads the terms out of ``documents.json`` (the packet and the
minutes) and, before the story is checked, rewrites a garbled term to the record's form. A term
with no certain record match loses its number or name and is described generically ("a
resolution"), so a garble is never printed.

The rewrite is conservative: a number matches when it is the record's number or the only record
number one digit away from it; an item phrase matches when one of its words is the record's word
and another is close to the record's spelling. Anything else is left to the checks that already
follow a name back to the claims, the captions and the documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any

#: Words that never make a phrase an item name on their own.
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "you",
        "your",
        "they",
        "their",
        "he",
        "she",
        "his",
        "her",
        "him",
        "them",
        "i",
        "me",
        "my",
        "not",
        "no",
        "yes",
        "town",
        "city",
        "county",
        "state",
        "colorado",
        "superior",
        "council",
        "board",
        "commission",
        "district",
        "authority",
        "meeting",
        "agenda",
        "item",
        "page",
        "packet",
        "minutes",
        "resolution",
        "ordinance",
        "number",
        "series",
        "section",
        "mayor",
        "councilmember",
        "member",
        "members",
        "staff",
        "public",
        "comment",
        "comments",
        "new",
        "old",
        "final",
        "draft",
    }
)
#: Words that open a sentence or a clause; capitalised there, they are not a name.
_OPENERS = frozenset(
    {
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
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "it",
        "we",
        "they",
        "he",
        "she",
    }
)
_MONTHS = frozenset(
    {
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
    }
)
#: A run of capitalised words: an item, company or place name as a document prints it.
_RUN_RE = re.compile(r"\b[A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*){0,4}\b")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
#: "Resolution Number R-91", "Ordinance No. O-12", "Resolution SMD#1-9".
_NUMBER_IN_DOC = re.compile(
    r"\b(?:Resolution|Ordinance)\s*(?:Number|No\.?|#)?\s*"
    r"(?P<num>[A-Z]{1,4}(?:#\s*1)?[ -]?\d{1,5}(?:[ -]\d{1,5})?)\b"
)
#: An item number printed on its own line ("O-2026-59", "R-91"), as the minutes list them.
_NUMBER_STANDALONE = re.compile(
    r"\b(?P<num>(?:R|O|SMD|SMID|SURA)(?:#\s*1)?[ -]?\d{1,5}(?:[ -]\d{1,5})?)\b"
)
#: A number as a story prints it: "Resolution R-991, Series 2026".
_NUMBER_IN_TEXT = re.compile(
    r"\b(?P<kind>Resolution|Ordinance)\s+(?:Number\s+|No\.?\s+|#\s*)?"
    r"(?P<num>[A-Z]{1,4}(?:#\s*1)?[ -]?\d{1,5}(?:[ -]\d{1,5})?)"
    r"(?P<tail>\s*,?\s*Series\s+\d{4})?",
    re.IGNORECASE,
)
#: A number the captions mangled into words: "Resolution Sura dash 2 Series 2026".
_GARBLED_NUMBER = re.compile(
    r"\b(?P<kind>Resolution|Ordinance)\s+"
    r"(?P<words>[A-Za-z]+(?:\s+[A-Za-z]+){0,3})\s+dash\s+(?P<num>\d{1,4})"
    r"(?P<tail>\s*,?\s+Series\s+\d{4})?",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Number:
    """One resolution or ordinance number as the documents print it, with its item text."""

    printed: str
    normalized: str
    context: str


@dataclass(frozen=True)
class Record:
    """The terms one meeting's documents hold."""

    numbers: tuple[Number, ...] = ()
    phrases: tuple[str, ...] = ()
    recusals: tuple[str, ...] = ()
    text: str = ""

    @property
    def empty(self) -> bool:
        return not (self.numbers or self.phrases or self.recusals)

    def number(self, printed: str) -> Number | None:
        key = normalize_number(printed)
        for number in self.numbers:
            if number.normalized == key:
                return number
        return None

    def verbatim(self, phrase: str) -> bool:
        """Whether the record prints this phrase (a company, item or place)."""
        needle = _normal(phrase)
        return bool(needle) and f" {needle} " in f" {_normal(self.text)} "

    def recusal_backed(self, label: str) -> bool:
        """Whether a document sentence states a recusal by this seat or person."""
        wanted = _normal(label)
        if not wanted:
            return False
        return any(wanted in _normal(sentence) for sentence in self.recusals)


def normalize_number(printed: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", printed).upper()


def _normal(text: str) -> str:
    lowered = text.replace("’", "'").replace("–", "-").replace("—", "-").lower()
    return " ".join(re.sub(r"[^a-z0-9'\- ]+", " ", lowered).split())


def _tokens(text: str) -> list[str]:
    return [token for token in _normal(text).replace("-", " ").split() if token]


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _phrase_run(text: str) -> bool:
    """A capitalised run that can name an item, company or place (not a heading)."""
    words = _WORD_RE.findall(text)
    if not words or len(words) > 4:
        return False
    if any(len(word) < 2 for word in words):
        return False
    if any(word.lower() in _MONTHS for word in words):
        return False
    lowered = [word.lower() for word in words]
    if all(word in _STOP for word in lowered):
        return False
    if lowered[0] in _OPENERS and len(words) == 1:
        return False
    # An all-caps document heading ("NOBLEWINS BENCH SWING") is the record's form, but not one
    # to print in a sentence; a mixed-case run is the item's name as the packet writes it.
    return any(word != word.upper() for word in words)


def load_record(pages: dict[str, Any] | None) -> Record | None:
    """The terms of every page of a meeting's documents, or None without documents."""
    if not pages:
        return None
    texts = [str(getattr(page, "text", "") or "") for page in pages.values()]
    text = "\n".join(part for part in texts if part)
    if not text.strip():
        return None
    numbers: dict[str, Number] = {}
    for pattern in (_NUMBER_IN_DOC, _NUMBER_STANDALONE):
        for match in pattern.finditer(text):
            printed = match.group("num").strip()
            key = normalize_number(printed)
            if not key or key in numbers:
                continue
            start = max(0, match.start() - 40)
            numbers[key] = Number(
                printed=printed.replace("  ", " "),
                normalized=key,
                context=text[start : match.end() + 250],
            )
    phrases: dict[str, None] = {}
    for match in _RUN_RE.finditer(text):
        run = " ".join(match.group(0).split())
        if _phrase_run(run):
            phrases.setdefault(run, None)
    recusals = tuple(
        sentence
        for sentence in _SENTENCE_RE.split(text.replace("\n", " "))
        if re.search(r"\brecus\w*|\bconflict of interest\b", sentence, re.IGNORECASE)
    )
    return Record(
        numbers=tuple(numbers.values()), phrases=tuple(phrases), recusals=recusals, text=text
    )


def _fuzzy_number(record: Record, printed: str) -> Number | None:
    """The record number the captions meant, when exactly one is one or two characters away."""
    key = normalize_number(printed)
    if not key:
        return None
    best: tuple[float, Number] | None = None
    for number in record.numbers:
        if key[:1] != number.normalized[:1]:
            continue
        score = _ratio(key, number.normalized)
        if best is None or score > best[0]:
            best = (score, number)
    if best is None or best[0] < 0.8:
        return None
    # A second number as close as the best is no certain match.
    close = [n for n in record.numbers if _ratio(key, n.normalized) >= best[0]]
    return best[1] if len(close) == 1 else None


def _context_score(record: Record, number: Number, following: str) -> int:
    wanted = {_singular(t) for t in _tokens(following) if t not in _STOP}
    if not wanted:
        return 0
    held = {_singular(t) for t in _tokens(number.context) if t not in _STOP}
    return len(wanted & held)


def _garbled_number(record: Record, following: str) -> Number | None:
    """The record number a garbled phrase describes, by the item words that follow it."""
    scored = [(_context_score(record, number, following), number) for number in record.numbers]
    scored = [pair for pair in scored if pair[0] > 0]
    if not scored:
        return None
    best = max(pair[0] for pair in scored)
    winners = [number for score, number in scored if score == best]
    if best < 2 or len(winners) != 1:
        return None
    return winners[0]


#: A story phrase that may name an item: a capitalised word and the words that follow it
#: ("Nouvel wind swings", "Noblewins Bench Swing").
_ITEM_SPAN = re.compile(r"\b[A-Z][a-z][A-Za-z'’\-]*(?:\s+[a-z][A-Za-z'’\-]*){0,3}\b")
#: Lower-case words that say nothing about which item is meant.
_COMMON = frozenset(
    {
        "resolution",
        "ordinance",
        "approve",
        "approves",
        "approved",
        "adopt",
        "adopts",
        "adopted",
        "purchase",
        "purchases",
        "install",
        "installation",
        "project",
        "projects",
        "program",
        "programs",
        "service",
        "services",
        "agreement",
        "contract",
        "contracts",
        "budget",
        "amendment",
        "amendments",
        "city",
        "town",
        "council",
        "board",
        "meeting",
        "public",
        "comment",
        "item",
        "items",
        "report",
        "reports",
        "plan",
        "plans",
        "update",
        "new",
    }
)


def _near_word(word: str, record: Record) -> str | None:
    """The one record proper noun an ASR spelling is close to ("Nouvel" -> "Noblewins")."""
    best: tuple[float, str] | None = None
    for phrase in record.phrases:
        for token in _WORD_RE.findall(phrase):
            if (
                len(token) < 4
                or token.lower() in _STOP
                or token.lower() in _COMMON
                or not token[0].isupper()
                or token[:2].lower() != word[:2].lower()
            ):
                continue
            score = _ratio(word.lower(), token.lower())
            if score >= 0.4 and (best is None or score > best[0]):
                best = (score, token)
    if best is None:
        return None
    close = [
        token
        for phrase in record.phrases
        for token in _WORD_RE.findall(phrase)
        if token[0].isupper()
        and token[:2].lower() == word[:2].lower()
        and _ratio(word.lower(), token.lower()) >= best[0]
    ]
    return best[1] if len(set(close)) == 1 else None


def _garbled_item(text: str, record: Record) -> list[tuple[int, int, str]]:
    """(start, end, replacement) for each item phrase whose first word is an ASR garble.

    "Nouvel wind swings" has the record's own head words ("wind", "swings"); the record names
    the company "Noblewins". A first word the record does not hold, next to words it does, is
    replaced by the record's word. A phrase with no record word to put there is left alone: it
    is ordinary prose (a sentence's first word), and the checks for names and numbers report a
    real garble.
    """
    out: list[tuple[int, int, str]] = []
    for match in _ITEM_SPAN.finditer(text):
        phrase = match.group(0)
        words = _WORD_RE.findall(phrase)
        if not words:
            continue
        first = words[0]
        if len(first) < 5 or first.lower() in _STOP or first.lower() in _MONTHS:
            continue
        if "'" in first or "’" in first:
            # "Work's" is part of the company's name, not a garble to strip.
            continue
        if re.search(r"(?:ing|ed|ly|tion|sion|ment|ness|ous|ive|able|ible|al|ic)$", first.lower()):
            # An ordinary English word ("Eliminating the vendor fee") is prose, not a name.
            continue
        if record.verbatim(first):
            continue
        content = [_singular(word.lower()) for word in words[1:] if word.lower() not in _STOP]
        if not content:
            # A bare garbled proper noun ("three Nouvel"): match it to the record's word.
            near = _near_word(first, record)
            if near is not None:
                out.append((match.start(), match.start() + len(first), near))
            continue
        held_phrases: list[str] = []
        for candidate in record.phrases:
            held = [_singular(token) for token in _tokens(candidate) if token not in _STOP]
            if any(word in held for word in content):
                held_phrases.append(candidate)
        if not held_phrases:
            continue
        best: tuple[float, str] | None = None
        for candidate in held_phrases:
            for token in _WORD_RE.findall(candidate):
                if (
                    len(token) < 4
                    or token.lower() in _STOP
                    or token.lower() in _COMMON
                    or not token[0].isupper()
                    or token.lower() in content
                ):
                    continue
                if token[:2].lower() != first[:2].lower():
                    continue
                score = _ratio(first.lower(), token.lower())
                if score >= 0.35 and (best is None or score > best[0]):
                    best = (score, token)
        if best is not None and best[0] >= 0.4:
            out.append((match.start(), match.start() + len(first), best[1]))
    return out


def _replace_generic(kind: str, at_sentence_start: bool) -> str:
    article = "A" if at_sentence_start else "a"
    return f"{article} {kind.lower()}"


def repair_text(text: str, record: Record | None) -> tuple[str, list[str]]:
    """Print the record's resolution number and item name; drop a term with no record match."""
    if record is None:
        return text, []
    changes: list[str] = []

    def garbled(match: re.Match[str]) -> str:
        following = text[match.end() : match.end() + 240]
        prefix = normalize_number(match.group("words"))
        digits = match.group("num")
        named = [
            number
            for number in record.numbers
            if len(prefix) >= 3
            and number.normalized.startswith(prefix)
            and number.normalized.endswith(digits)
        ]
        found = named[0] if len(named) == 1 else _garbled_number(record, following)
        if found is not None:
            changes.append(f"term {match.group(0)!r} -> {found.printed!r}")
            return f"{match.group('kind')} {found.printed}, Series 2026"
        changes.append(f"term {match.group(0)!r} -> a generic resolution (no record match)")
        at_start = bool(re.search(r"(^|[.!?]\s+)$", text[: match.start()]))
        return _replace_generic(match.group("kind"), at_start)

    text = _GARBLED_NUMBER.sub(garbled, text)

    def numbered(match: re.Match[str]) -> str:
        printed = match.group("num").strip()
        found = record.number(printed) or _fuzzy_number(record, printed)
        if found is None:
            changes.append(f"term {match.group(0)!r} -> a generic resolution (no record match)")
            at_start = bool(re.search(r"(^|[.!?]\s+)$", text[: match.start()]))
            return _replace_generic(match.group("kind"), at_start)
        tail = match.group("tail") or ""
        new = f"{match.group('kind')} {found.printed}{tail}"
        if new != match.group(0):
            changes.append(f"term {match.group(0)!r} -> {new!r}")
        return new

    text = _NUMBER_IN_TEXT.sub(numbered, text)

    def lone(match: re.Match[str]) -> str:
        printed = match.group("num").strip()
        found = record.number(printed) or _fuzzy_number(record, printed)
        if found is None:
            return match.group(0)
        if found.printed != printed:
            changes.append(f"term {printed!r} -> {found.printed!r}")
        return found.printed

    text = _NUMBER_STANDALONE.sub(lone, text)

    garbles = _garbled_item(text, record)
    for start, end, replacement in reversed(garbles):
        changes.append(f"term {text[start:end]!r} -> {replacement!r}")
        text = text[:start] + replacement + text[end:]
    return text, changes


def term_issues(text: str, record: Record | None) -> list[tuple[str, str]]:
    """(code, message) for each record term the text prints in a form the record does not hold."""
    if record is None or record.empty:
        return []
    out: list[tuple[str, str]] = []
    for match in _GARBLED_NUMBER.finditer(text):
        out.append(
            (
                "record_term",
                (
                    f"resolution {match.group(0)!r} is not in the meeting documents; "
                    "use the record's number or describe the item generically"
                ),
            )
        )
    seen: set[str] = set()
    for pattern in (_NUMBER_IN_TEXT, _NUMBER_STANDALONE):
        for match in pattern.finditer(text):
            printed = match.group("num").strip()
            key = normalize_number(printed)
            if key in seen:
                continue
            seen.add(key)
            exact = record.number(printed)
            if exact is not None:
                continue
            near = _fuzzy_number(record, printed)
            if near is not None:
                out.append(
                    (
                        "record_term",
                        (
                            f"number {printed!r} must be printed as the record prints it: "
                            f"{near.printed!r}"
                        ),
                    )
                )
                continue
            out.append(
                (
                    "record_term",
                    (
                        f"number {printed!r} is not in the meeting documents; "
                        "use the record's number or drop it"
                    ),
                )
            )
    for start, end, _ in _garbled_item(text, record):
        out.append(
            (
                "record_term",
                (
                    f"item {text[start:end]!r} is not the meeting documents' name; "
                    "use the record's spelling or drop it"
                ),
            )
        )
    return out


def repair_terms(article: Any, record: Record | None) -> tuple[Any, list[str]]:
    """Repair every record term in a story, in place of :class:`Article` (same shape)."""
    if record is None:
        return article, []
    changes: list[str] = []

    def fix(text: str) -> str:
        new, lines = repair_text(text, record)
        changes.extend(lines)
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
    votes = tuple(replace(vote, motion=fix(vote.motion)) for vote in article.votes)
    return (
        replace(
            article,
            headline=fix(article.headline),
            dek=fix(article.dek),
            sections=sections,
            votes=votes,
        ),
        changes,
    )
