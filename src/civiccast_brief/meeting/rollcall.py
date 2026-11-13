"""Members of a body, read from the roll call in the body's own minutes (plain parsing, no model).

The roster is the only source of speaker names, and a city's setup cannot always fill it in from a
web page. A meeting's own public records often print the members: the roll call of the minutes
("Those present were Mayor Susie Hidalgo-Fahring and Council Members Diane Crist, ..."), a
commission's "Present on the Commission were ...", a board's "Board Members Present at Roll
Call: ...", and a member list on the agenda. This module reads those lines.

Every member returned carries where it was read: the document, the page, the line on that page and
the sentence. A name is returned only when a record prints it in full (two or more words, each
capitalised); a surname alone ("Chair Polan") is not a name this module can know, so it is left out
and counted. Staff, liaisons from another body, and the clerk who called the roll are not members.

Which body a roll call belongs to is read from the heading of the minutes it sits in, so the
council's packet that holds a board's minutes does not give the council that board's members.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from civiccast_brief.citybuild import members as page_members

MONTHS: Final = [
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
]
_DATE: Final = re.compile(
    rf"\b(?P<month>{'|'.join(MONTHS)})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})\b", re.IGNORECASE
)
#: The roll call's own heading ("2. Roll Call", "ROLL CALL - PLEDGE OF ALLEGIANCE", "Attendance").
_HEADING: Final = re.compile(
    r"^\W*(?:\d+\s*[.)]\s*)?(?:roll\s*call|call(?:ing)?\s+(?:of\s+)?the\s+roll|attendance)\b",
    re.IGNORECASE,
)
#: A line that starts the next numbered item of the minutes ends the roll call.
_NEXT_ITEM: Final = re.compile(r"^\s*\d+\s*[.)]\s*(?:\S|$)")
_FIRST_ITEM: Final = re.compile(r"^\s*1\s*[.)]")
_STAFF: Final = re.compile(
    r"^(?:other\s+|city\s+|town\s+|county\s+)?(?:staff|officials)\b|^in\s+attendance\b|^also\b",
    re.IGNORECASE,
)
_PRESENT: Final = re.compile(
    r"\b(?:present|in\s+attendance|attending)\b[^:]*?(?::|\bwere\b|\bwas\b)\s*(?P<list>.+)$",
    re.IGNORECASE,
)
_ABSENT_AFTER: Final = re.compile(
    r"^(?P<list>.+?)\s+(?:were|was|are|is)\s+(?:also\s+)?(?:absent|excused)\b", re.IGNORECASE
)
_ABSENT_LEAD: Final = re.compile(
    r"^(?P<lead>[^:]*\b(?:absent|excused)\b[^:]*):\s*(?P<list>.+)$", re.IGNORECASE
)
#: A line that opens with a label of its own ("Board Members Excused: ...").
_LABEL: Final = re.compile(
    r"^[A-Z][\w' -]{0,40}(?:present|excused|absent)[^:.]{0,30}:", re.IGNORECASE
)
_ARRIVED: Final = re.compile(r"^(?P<who>.+?)\s+arrived\b", re.IGNORECASE)
_TITLE: Final = re.compile(
    r"^(?P<title>mayor\s+pro[\s-]*tem(?:pore)?|deputy\s+mayor|vice\s+mayor|mayor|vice[\s-]*chair"
    r"|chair(?:man|woman|person)?|council[\s-]*members?|council\s+representatives?"
    r"|councilman|councilwoman|councilor|commissioners?|board\s+members?|trustees?|directors?"
    r"|members?)\b\s*:?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_NORMAL: Final = {
    "mayor": "Mayor",
    "mayor pro tem": "Mayor Pro Tem",
    "mayor pro tempore": "Mayor Pro Tem",
    "deputy mayor": "Deputy Mayor",
    "vice mayor": "Vice Mayor",
    "vice chair": "Vice Chair",
    "chair": "Chair",
    "chairman": "Chair",
    "chairwoman": "Chair",
    "chairperson": "Chair",
    "council member": "Council Member",
    "councilmember": "Council Member",
    "councilman": "Council Member",
    "councilwoman": "Council Member",
    "councilor": "Council Member",
    "council representative": "Council Representative",
    "commissioner": "Commissioner",
    "board member": "Board Member",
    "trustee": "Trustee",
    "director": "Director",
    "member": "Member",
}
#: Titles of a member of the city council, which on any other body's roll call mark the council's
#: liaison to that body, who is not a member of it.
_COUNCIL_TITLES: Final = frozenset({"Council Member", "Council Representative"})
_ABBREVIATION: Final = re.compile(r"(?:\b[A-Z]|\b(?:Jr|Sr|Dr|Mr|Mrs|Ms))\.$")
_CONTACT: Final = re.compile(r"\S+@\S+|\(?\d{3}\)?[-.\s]\d{3}[-.]\d{4}")
#: Pages after a minutes heading that still count as that minutes document.
_CARRY_PAGES: Final = 3
_MAX_SECTION_LINES: Final = 14


@dataclass(frozen=True)
class Sighting:
    """One member as one record prints them."""

    name: str
    first: str
    last: str
    role: str
    status: str  # "present" or "absent"
    document: str  # "packet", "minutes", "agenda"
    url: str
    page: int  # page of the PDF; 0 for a text agenda
    line: int  # line on that page (1-based)
    held: str  # ISO date of the meeting whose roll call it is
    text: str  # the sentence it was read from, without contact details


@dataclass
class Reading:
    """What was read: the sightings, and the names the records printed without enough to use."""

    sightings: list[Sighting]
    surname_only: list[str]
    liaisons: list[str]


def _normal(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def page_lines(text: str) -> list[str]:
    """The page's lines, with the line numbers some minutes print in front of each line removed
    ("13Recording Assistant ..." is line 13)."""
    lines = [line.rstrip() for line in text.replace("\xa0", " ").split("\n")]
    if len(lines) >= 8:
        numbered = sum(1 for index, line in enumerate(lines) if line.startswith(str(index + 1)))
        if numbered >= 0.6 * len(lines):
            lines = [line.removeprefix(str(index + 1)) for index, line in enumerate(lines)]
    return lines


def _words(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _header(lines: Sequence[str]) -> str:
    """The heading of a page that starts a minutes document: up to the first numbered item."""
    kept: list[str] = []
    for line in lines[:12]:
        if _FIRST_ITEM.match(line):
            break
        kept.append(line.strip())
    return " ".join(part for part in kept if part)


def _is_minutes(header: str) -> bool:
    return re.search(r"\bminutes\b", header, re.IGNORECASE) is not None


def _names_body(header: str, body: str) -> bool:
    wanted = _words(body) - {"the", "of", "and"}
    return bool(wanted) and wanted <= _words(header)


def held_on(header: str) -> str:
    found = _DATE.search(header)
    if not found:
        return ""
    try:
        return date(
            int(found["year"]), MONTHS.index(found["month"].casefold()) + 1, int(found["day"])
        ).isoformat()
    except ValueError:
        return ""


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
        if out and _ABBREVIATION.search(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def _default_role(body: str) -> str:
    low = body.casefold()
    if "council" in low:
        return "Council Member"
    if "commission" in low and "commissioners" not in low:
        return "Commissioner"
    if "commissioner" in low:
        return "Commissioner"
    if "trustee" in low:
        return "Trustee"
    return "Board Member"


def _entries(listed: str, body: str) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """``(name, role)`` for each person in a list, the surname-only entries, the liaisons."""
    pieces = re.split(r",|;|\band\b", re.sub(r"\([^)]*\)", " ", listed))
    named: list[tuple[str, str]] = []
    surnames: list[str] = []
    liaisons: list[str] = []
    carried = ""
    for piece in pieces:
        rest, title = _normal(piece).strip(" ."), ""
        while True:
            found = _TITLE.match(rest)
            if not found:
                break
            printed = " ".join(found["title"].casefold().replace("-", " ").split())
            plural = printed.endswith("s")
            printed = printed[:-1] if plural else printed
            title = title or _NORMAL.get(printed, "")
            rest = found["rest"].strip(" .")
            if plural:
                carried = _NORMAL.get(printed, "")
        if not rest:
            continue
        role = title or carried or _default_role(body)
        name = page_members.as_name(rest)
        if name is None:
            if re.fullmatch(r"[A-Z][A-Za-z'’-]+", rest):
                surnames.append(rest)
            continue
        if role in _COUNCIL_TITLES and "council" not in body.casefold():
            liaisons.append(name)
            continue
        named.append((name, role))
    return named, surnames, liaisons


def _read_section(
    sentences: Iterable[tuple[str, int]], body: str
) -> tuple[list[tuple[str, str, str, int, str]], list[str], list[str]]:
    """``(name, role, status, line, sentence)`` from the sentences of a roll call."""
    out: list[tuple[str, str, str, int, str]] = []
    surnames: list[str] = []
    liaisons: list[str] = []
    about_staff = False
    for sentence, line in sentences:
        if _STAFF.match(sentence):
            about_staff = True
            continue
        status, listed = "", ""
        absent = _ABSENT_AFTER.match(sentence) or _ABSENT_LEAD.match(sentence)
        arrived = _ARRIVED.match(sentence)
        present = _PRESENT.search(sentence)
        if absent and not re.search(r"\bliaison\b", sentence, re.IGNORECASE):
            status, listed = "absent", absent["list"]
        elif present and not re.search(r"\bliaison\b|\bstaff\b", sentence[:60], re.IGNORECASE):
            status, listed = "present", present["list"]
        elif arrived and not about_staff:
            status, listed = "present", arrived["who"]
        if not status:
            continue
        about_staff = False
        got, short, liaison = _entries(listed, body)
        surnames += short
        liaisons += liaison
        out += [(name, role, status, line, sentence) for name, role in got]
    return out, surnames, liaisons


def _page_lines_of(page: Mapping[str, Any]) -> list[str]:
    return page_lines(str(page.get("text", "")))


def read_documents(documents: Sequence[Mapping[str, Any]], body: str, day: date) -> Reading:
    """The roll calls in a meeting's documents (``documents.json``'s ``documents``) that belong to
    ``body``. A roll call without a date in its heading is dated ``day``."""
    sightings: list[Sighting] = []
    surnames: list[str] = []
    liaisons: list[str] = []
    for document in documents:
        pages = [p for p in document.get("pages", []) if isinstance(p, Mapping)]
        current, held, carry = "", "", 0
        for index, page in enumerate(pages):
            lines = _page_lines_of(page)
            header = _header(lines)
            if _is_minutes(header):
                current, held, carry = header, held_on(header), _CARRY_PAGES
            elif carry:
                carry -= 1
            else:
                current = ""
            if not current or not _names_body(current, body):
                continue
            for start, line in enumerate(lines):
                if not _HEADING.match(line) or len(line.strip()) > 60:
                    continue
                collected = _section(lines, start, pages, index)
                if not collected:
                    continue
                got, short, liaison = _read_section(_with_lines(collected), body)
                surnames += short
                liaisons += liaison
                for name, role, status, at, sentence in got:
                    first, last = page_members.split_name(name)
                    sightings.append(
                        Sighting(
                            name=name,
                            first=first,
                            last=last,
                            role=role,
                            status=status,
                            document=str(document.get("kind", "")),
                            url=str(document.get("url", "")),
                            page=int(page.get("page", index + 1)),
                            line=at,
                            held=held or day.isoformat(),
                            text=_CONTACT.sub("", sentence)[:400],
                        )
                    )
    return Reading(sightings, sorted(set(surnames)), sorted(set(liaisons)))


def _section(
    lines: Sequence[str], start: int, pages: Sequence[Mapping[str, Any]], index: int
) -> list[tuple[int, str]]:
    """The lines under the heading at ``start`` as ``(line number, text)``, up to the next numbered
    item. When the page ends first, the next page's opening lines go on with it."""
    out: list[tuple[int, str]] = []
    run = list(enumerate(lines))[start + 1 :]
    for number, text in run:
        if _NEXT_ITEM.match(text) or len(out) >= _MAX_SECTION_LINES:
            return out
        if text.strip():
            out.append((number + 1, text.strip()))
    joined = " ".join(t for _, t in out)
    if out and not joined.rstrip().endswith((".", "!", "?")) and index + 1 < len(pages):
        for number, text in enumerate(_page_lines_of(pages[index + 1])):
            if _NEXT_ITEM.match(text) or len(out) >= _MAX_SECTION_LINES:
                break
            if text.strip():
                out.append((number + 1, text.strip()))
    return out


def _with_lines(collected: Sequence[tuple[int, str]]) -> list[tuple[str, int]]:
    """The section's sentences, each with the line it starts on."""
    text, starts = "", []
    for number, line in collected:
        if text and _LABEL.match(line) and not text.rstrip().endswith((".", "!", "?")):
            text = (
                text.rstrip() + "."
            )  # a labelled line ("City Staff Members Present: ...") starts one
        joiner = "" if text.endswith("-") else (" " if text else "")
        starts.append((len(text) + len(joiner), number))
        text += joiner + line
    out: list[tuple[str, int]] = []
    at = 0
    for sentence in _sentences(text):
        position = text.find(sentence, at)
        at = position + len(sentence)
        line = next((n for off, n in reversed(starts) if off <= position), starts[0][1])
        out.append((sentence, line))
    return out


def read_agenda(text: str, body: str, day: date) -> list[Sighting]:
    """A member list printed on the agenda: lines that pair a name with a seat title, three or more
    (the way the city's own members page is read). Lines are those of ``agenda.txt``."""
    found, _ = page_members.read_members(text, body)
    if len(found) < page_members.MIN_MEMBERS:
        return []
    lines = text.split("\n")
    url = lines[0][len("Source: ") :].strip() if lines and lines[0].startswith("Source: ") else ""
    out: list[Sighting] = []
    for member in found:
        at = next((n for n, line in enumerate(lines, 1) if member.name in line), 0)
        out.append(
            Sighting(
                name=member.name,
                first=member.first,
                last=member.last,
                role=member.role,
                status="present",
                document="agenda",
                url=url,
                page=0,
                line=at,
                held=day.isoformat(),
                text=_CONTACT.sub("", lines[at - 1].strip())[:400] if at else "",
            )
        )
    return out
