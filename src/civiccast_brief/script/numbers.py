"""Spoken numbers to digits, for the script writer and the script checker.

Numbers are read the way a radio host says them: "twenty twenty-six" is 2026, and
"eighty-five thirteen" is 8513 (a street number read as two pairs). "hundred", "thousand"
and "million" make ordinary numbers. "dash" and "to" between two numbers join them with a
hyphen, and "point" makes a decimal point. A month name followed by "first" to "thirty-first"
makes an ordinal day. Other ordinals ("first reading", "Ninth Avenue") stay words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNIT_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
TENS_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
MULTIPLIER_WORDS = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
ORDINAL_UNIT_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
}
ORDINAL_TENS_WORDS = {
    "twentieth": 20,
    "thirtieth": 30,
    "fortieth": 40,
    "fiftieth": 50,
    "sixtieth": 60,
    "seventieth": 70,
    "eightieth": 80,
    "ninetieth": 90,
}
MONTH_WORDS = frozenset(
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
    }
)
# "one" is a pronoun or idiom in these positions, not a number.
NUMBER_WORDS = frozenset(
    {
        *UNIT_WORDS,
        *TENS_WORDS,
        *MULTIPLIER_WORDS,
        "hundred",
        *ORDINAL_UNIT_WORDS,
        *ORDINAL_TENS_WORDS,
    }
)
_ONE_NOT_NUMBER_BEFORE = frozenset({"no", "some", "any", "every", "each"})
_ONE_NOT_NUMBER_AFTER = frozenset(
    {"of", "another", "more", "thing", "things", "day", "way", "side", "sides", "by", "at", "after"}
)
# A word between two numbers that joins them, and the text it becomes.
_CONNECTORS = {"dash": "-", "to": "-", "point": "."}

_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:,\d{3})*(?:\.\d+)?")
_SPACE_RE = re.compile(r"\s+")
_NUMERIC_GAP_RE = re.compile(r"\s+|\s*-\s*")
_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?")
_THOUSANDS_COMMA_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


@dataclass(frozen=True)
class _Token:
    text: str
    lower: str
    start: int
    end: int
    is_digits: bool


@dataclass(frozen=True)
class _Chunk:
    """One standard number, such as "two hundred thousand" or "twenty-six"."""

    value: int
    multiplied: bool
    ordinal: bool
    end: int


def _tokenize(text: str) -> list[_Token]:
    tokens = []
    for match in _TOKEN_RE.finditer(text):
        token_text = match.group(0)
        tokens.append(
            _Token(
                text=token_text,
                lower=token_text.lower(),
                start=match.start(),
                end=match.end(),
                is_digits=token_text[0].isdigit(),
            )
        )
    return tokens


def _gap(text: str, left: _Token, right: _Token) -> str:
    return text[left.end : right.start]


def _one_is_idiom(tokens: list[_Token], index: int) -> bool:
    if index > 0 and tokens[index - 1].lower in _ONE_NOT_NUMBER_BEFORE:
        return True
    return index + 1 < len(tokens) and tokens[index + 1].lower in _ONE_NOT_NUMBER_AFTER


def _read_chunk(tokens: list[_Token], text: str, start: int, ordinal_ok: bool) -> _Chunk | None:
    """Read one standard number from the word tokens beginning at ``start``."""
    total = 0
    current = 0
    multiplied = False
    ordinal = False
    last: str | None = None
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token.is_digits:
            break
        if index > start and not _NUMERIC_GAP_RE.fullmatch(_gap(text, tokens[index - 1], token)):
            break
        word = token.lower
        if word == "one" and _one_is_idiom(tokens, index):
            break
        if word in UNIT_WORDS:
            value = UNIT_WORDS[word]
            if last == "unit" or (last == "tens" and value >= 10):
                break
            current += value
            last = "unit"
        elif word in TENS_WORDS:
            if last in ("unit", "tens"):
                break
            current += TENS_WORDS[word]
            last = "tens"
        elif word == "hundred":
            if last not in ("unit", "tens") or current == 0:
                break
            current *= 100
            multiplied = True
            last = "hundred"
        elif word in MULTIPLIER_WORDS:
            if last is None or last == "mult":
                break
            total += current * MULTIPLIER_WORDS[word]
            current = 0
            multiplied = True
            last = "mult"
        elif word == "and" and last in ("hundred", "mult") and _next_is_number(tokens, text, index):
            index += 1
            continue
        elif word in ORDINAL_UNIT_WORDS:
            if last is None and not ordinal_ok:
                break
            if last not in (None, "tens"):
                break
            if last == "tens" and not _HYPHEN_RE.fullmatch(_gap(text, tokens[index - 1], token)):
                break
            current += ORDINAL_UNIT_WORDS[word]
            ordinal = True
            last = "unit"
            index += 1
            break
        elif word in ORDINAL_TENS_WORDS:
            if last is not None or not ordinal_ok:
                break
            current += ORDINAL_TENS_WORDS[word]
            ordinal = True
            last = "tens"
            index += 1
            break
        else:
            break
        index += 1
    if last is None:
        return None
    return _Chunk(value=total + current, multiplied=multiplied, ordinal=ordinal, end=index)


_HYPHEN_RE = re.compile(r"\s*-\s*")


def _next_is_number(tokens: list[_Token], text: str, index: int) -> bool:
    if index + 1 >= len(tokens):
        return False
    nxt = tokens[index + 1]
    if nxt.is_digits or not _SPACE_RE.fullmatch(_gap(text, tokens[index], nxt)):
        return False
    return nxt.lower in UNIT_WORDS or nxt.lower in TENS_WORDS


def _number_at(tokens: list[_Token], text: str, index: int) -> tuple[str, int] | None:
    """Digits for the spoken number starting at ``index``, and the index after it."""
    if tokens[index].is_digits:
        return None
    ordinal_ok = (
        index > 0
        and tokens[index - 1].lower in MONTH_WORDS
        and bool(_SPACE_RE.fullmatch(_gap(text, tokens[index - 1], tokens[index])))
    )
    first = _read_chunk(tokens, text, index, ordinal_ok)
    if first is None:
        return None
    if first.multiplied or first.ordinal or first.value > 99:
        return str(first.value), first.end
    # Plain numbers said one after another are read as digits: "twenty twenty-six" is 2026,
    # "eighty-five thirteen" 8513, "eight five one three" 8513, "two eighty-seven" 287.
    # A two-digit number followed by a single digit is not joined: "sixty-nine six to one" stays
    # 69 and 6-1, because "six to one" is a vote.
    digits = str(first.value)
    last = first.value
    end = first.end
    while end < len(tokens) and not tokens[end].is_digits:
        if not _NUMERIC_GAP_RE.fullmatch(_gap(text, tokens[end - 1], tokens[end])):
            break
        nxt = _read_chunk(tokens, text, end, False)
        if nxt is None or nxt.multiplied or nxt.ordinal or nxt.value > 99:
            break
        if last >= 10 and nxt.value < 10:
            break
        digits += f"{nxt.value:02d}" if nxt.value >= 10 else str(nxt.value)
        last = nxt.value
        end = nxt.end
    return digits, end


def _right_side(tokens: list[_Token], text: str, connector_index: int) -> tuple[str, int] | None:
    """The text after a connector word, and the index after it.

    After "point" only single digits are read ("four point two five" is 4.25), so
    "four point five million" stays "4.5 million". After "dash" or "to" a whole number is read.
    """
    right_index = connector_index + 1
    if right_index >= len(tokens):
        return None
    if not _SPACE_RE.fullmatch(_gap(text, tokens[connector_index], tokens[right_index])):
        return None
    right = tokens[right_index]
    if right.is_digits:
        return right.text.replace(",", ""), right_index + 1
    if tokens[connector_index].lower != "point":
        return _number_at(tokens, text, right_index)
    word = right.lower
    if word not in UNIT_WORDS and word not in TENS_WORDS:
        return None
    digits = str(UNIT_WORDS.get(word, TENS_WORDS.get(word)))
    index = right_index + 1
    while (
        index < len(tokens)
        and tokens[index].lower in UNIT_WORDS
        and UNIT_WORDS[tokens[index].lower] < 10
        and _SPACE_RE.fullmatch(_gap(text, tokens[index - 1], tokens[index]))
    ):
        digits += str(UNIT_WORDS[tokens[index].lower])
        index += 1
    return digits, index


def spoken_to_digits(text: str) -> str:
    """Return ``text`` with spoken numbers replaced by digits.

    >>> spoken_to_digits("Ordinance twenty twenty-six dash sixty-nine")
    'Ordinance 2026-69'
    """
    tokens = _tokenize(text)
    pieces: list[str] = []
    position = 0
    index = 0
    while index < len(tokens):
        found = _number_at(tokens, text, index)
        if found is None:
            index += 1
            continue
        digits, after = found
        start = tokens[index].start
        end = tokens[after - 1].end
        while after + 1 < len(tokens) and tokens[after].lower in _CONNECTORS:
            connector = tokens[after]
            if not _SPACE_RE.fullmatch(_gap(text, tokens[after - 1], connector)):
                break
            right = _right_side(tokens, text, after)
            if right is None:
                break
            right_text, next_index = right
            if connector.lower == "point":
                digits = f"{digits}.{right_text}"
            else:
                digits = f"{digits}{_CONNECTORS[connector.lower]}{right_text}"
            end = tokens[next_index - 1].end
            after = next_index
        pieces.append(text[position:start])
        pieces.append(digits)
        position = end
        index = after
    pieces.append(text[position:])
    return "".join(pieces)


def number_atoms(text: str) -> frozenset[str]:
    """Every number in ``text`` as a digit string, after spoken numbers are converted.

    "twenty twenty-six dash sixty-nine" and "2026-69" both give {"2026", "69"}.
    Commas inside numbers are dropped, so "$200,000" gives {"200000"}.
    """
    converted = _THOUSANDS_COMMA_RE.sub("", spoken_to_digits(text))
    return frozenset(_DIGITS_RE.findall(converted))
