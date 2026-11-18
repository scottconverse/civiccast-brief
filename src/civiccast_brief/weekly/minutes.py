"""Council votes read from the official minutes, as Superior's clerk prints them.

A port of TownReporter's ``meeting-minutes.ts`` (design D214), adapted to the
minutes CivicClerk publishes for the Superior Town Council:

    Town Council
    Meeting Minutes
    October 12, 2026
    ...
    Consent Agenda

    Motion: Council Member Cracraft moved to approve the consent agenda,
    seconded by Council Member Miller.
    Vote: PASSED. 6 - 0, Absent - 0

TownReporter's reader looks for a standalone ``MOTION`` line and a
``Carried: 6-1`` tally. Superior prints ``Motion:`` and ``Vote:`` with the
dancer and the seconder in one sentence ("A moved to ..., seconded by B"), so
the motion is read from that sentence instead, and the tally from the Vote
line. Everything else is TownReporter's rule: one vote per item, the last
motion decides an item, and a procedural motion is not the item's decision.

The text is pure data: pages in, motions out. Nothing here fetches anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: The headings the clerk's minutes print above their items, in the order they print.
TOP_HEADINGS = (
    "Preliminary Matters",
    "Council Reports",
    "Committee Interviews",
    "Presentations",
    "Project Updates",
    "Consent Agenda",
    "Public Hearing",
    "Executive Session",
    "Action on Executive Session",
    "Adjournment",
)
_TOP = {h.casefold(): h for h in TOP_HEADINGS}

#: " a. Advisory Committee Interviews for PARC" -- a letter or roman numeral under a heading.
_SUB_ITEM = re.compile(r"^\s*([a-z]{1,4})\.\s+(\S.*)$")
#: A motion line, which may be followed by its vote on the same line.
_MOTION = re.compile(r"^\s*Motion:\s*(.*)$", re.IGNORECASE)
#: "Council Member Cracraft moved to approve the consent agenda, seconded by Council Member Miller."
_MOVED = re.compile(
    r"^(?P<mover>.+?)\s+moved\s+to\s+(?P<motion>.+?),\s+seconded\s+by\s+(?P<seconder>.+?)\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)
#: "Vote: PASSED. 6 - 0, Absent - 0" or "Vote: . 6 - 0, Absent - 0".
_VOTE = re.compile(
    r"^\s*Vote:\s*(?:(?P<result>PASSED|FAILED|CARRIED)\s*\.)?\s*[.\s]*"
    r"(?P<yes>\d{1,3})\s*[-–—]\s*(?P<no>\d{1,3})",
    re.IGNORECASE,
)
_ROLE = re.compile(
    r"^(?:Council\s*Member|Councilmember|Council\s*Person|Councill\s+Member|Mayor\s+Pro\s*Tem|"
    r"Mayor|President|Vice\s*President|Chair|Vice\s*Chair|Commissioner|Director|Chief|"
    r"Deputy\s+Chief|Town\s+Manager|Town\s+Attorney|Town\s+Clerk)\s+",
    re.IGNORECASE,
)
#: A motion that is not the item's decision: extending the meeting, recess, adjournment,
#: suspending the rules. An item whose heading says adjournment keeps its motion.
_PROCEDURAL = re.compile(
    r"^(?:extend\s+(?:the\s+meeting|past)|extend\b.*\bpast\b|(?:take\s+a\s+)?recess|"
    r"take\s+a\s+(?:short\s+)?break|adjourn|enter\s+executive\s+session|leave\s+executive\s+session|"
    r"suspend\s+the\s+rules)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MinutesMotion:
    """One motion as the minutes print it."""

    item: str
    """The item id: the heading's slug, with a sub-item letter when one is printed."""
    heading: str
    motion: str
    mover: str
    seconder: str
    """The names as the minutes print them, without their role ("Cracraft")."""
    tally: str
    result: str
    excerpt: str
    page: int

    @property
    def procedural(self) -> bool:
        return bool(_PROCEDURAL.match(self.motion))


def _clean_name(text: str) -> str:
    name = " ".join(text.replace(",", " ").split())
    name = name.rstrip(".")
    for _ in range(3):
        stripped = _ROLE.sub("", name).strip()
        if stripped == name:
            break
        name = stripped
    return name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


_TITLE = re.compile(
    r"^(?:Mayor\s+Pro[\s-]*Tem|Mayor|Council\s*Member|Councilmember|Council\s+Person)\s+",
    re.IGNORECASE,
)


def present_members(pages: tuple[str, ...] | list[str]) -> list[str]:
    """The members the minutes' own roll call lists under ``Present:``, in order.

    The roster file can lag the records; the minutes' Present list is the record of who
    sat that night. An empty ``Absent:`` list is common and is not a member.
    """
    names: list[str] = []
    inside = False
    for text in pages:
        for line in text.splitlines():
            folded = line.strip().casefold()
            if folded.startswith("present:"):
                inside = True
                continue
            if inside and folded.startswith(("absent:", "others present:", "town manager")):
                inside = False
                continue
            if not inside:
                continue
            stripped = line.strip()
            if not stripped or not _TITLE.match(stripped):
                continue
            rest = _TITLE.sub("", stripped, count=1).split(":", 1)[0].strip()
            words = rest.split()
            # A name is the capitalized run at the start of the line: "Mayor Mark Lacis
            # called to order..." gives "Mark Lacis", not the sentence.
            taken: list[str] = []
            for word in words[:3]:
                if not word[:1].isupper():
                    break
                taken.append(word)
            name = " ".join(taken)
            if len(taken) >= 2 and name not in names:
                names.append(name)
    return names


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def minutes_date(pages: tuple[str, ...] | list[str]) -> date | None:
    """The meeting day the minutes' own head prints, or None."""
    for text in pages[:2]:
        for month, day, year in re.findall(
            r"\b(January|February|March|April|May|June|July|August|September|October|"
            r"November|December)\s+(\d{1,2}),\s+(\d{4})\b",
            text,
        ):
            try:
                return date(int(year), _MONTHS.index(month) + 1, int(day))
            except ValueError:
                continue
    return None


def parse_motions(pages: tuple[str, ...] | list[str]) -> list[MinutesMotion]:
    """Every motion the minutes record, in the order they print.

    ``pages`` is the minutes document's pages, page 1 first. The item an id
    belongs to is the nearest heading above it; a motion under `` a.`` takes that
    sub-item's id (``consent-agenda-a``), so two motions under one heading are
    kept apart.
    """
    motions: list[MinutesMotion] = []
    top = ""
    sub = ""
    block: list[str] = []
    block_page = 1

    def flush() -> None:
        nonlocal block
        if not block:
            return
        vote_at = next((i for i, line in enumerate(block) if _VOTE.match(line)), None)
        vote = _VOTE.match(block[vote_at]) if vote_at is not None else None
        spoken = " ".join(block if vote_at is None else block[:vote_at])
        block = []
        moved = _MOVED.match(spoken.strip())
        if not moved or not vote:
            return
        motion = " ".join(moved.group("motion").split()).strip(" .")
        if not motion:
            return
        item = _slug(top or "motion")
        if sub:
            item = f"{item}-{sub}"
        motions.append(
            MinutesMotion(
                item=item,
                heading=top + (f" {sub}." if sub else ""),
                motion=motion,
                mover=_clean_name(
                    re.sub(r"^\s*Motion:\s*", "", moved.group("mover"), flags=re.IGNORECASE)
                ),
                seconder=_clean_name(moved.group("seconder")),
                tally=f"{int(vote.group('yes'))}-{int(vote.group('no'))}",
                result="Failed" if (vote.group("result") or "").upper() == "FAILED" else "Passed",
                excerpt=" ".join(f"{spoken} {vote.group(0)}".split())[:400],
                page=block_page,
            )
        )

    for number, text in enumerate(pages, start=1):
        for line in text.replace("\f", "\n").splitlines():
            stripped = line.strip()
            # "Preliminary Matters (6:00 p.m.)" is the heading "Preliminary Matters".
            folded = re.sub(r"\s*\([^)]*\)", "", stripped).casefold().rstrip(":").strip()
            if folded in _TOP:
                flush()
                top = _TOP[folded]
                sub = ""
                continue
            motion = _MOTION.match(line)
            if motion:
                flush()
                block = [line]
                block_page = number
                if _VOTE.match(line[motion.start(1) :]):
                    flush()
                continue
            if block:
                block.append(line)
                if _VOTE.match(line):
                    flush()
                continue
            item = _SUB_ITEM.match(line)
            if item and top:
                sub = item.group(1)
    flush()
    return motions


def deciding_motions(motions: list[MinutesMotion]) -> list[MinutesMotion]:
    """One motion per item: the last that is not procedural, or the last at an adjournment.

    This is TownReporter's ``minutesVoteRecords`` rule: an item can be moved on
    more than once (a budget and its fee schedule, an extension after the vote),
    and only the last motion that decided the item is the item's outcome.
    """
    by_item: dict[str, list[MinutesMotion]] = {}
    for motion in motions:
        by_item.setdefault(motion.item, []).append(motion)
    chosen: list[MinutesMotion] = []
    for item_motions in by_item.values():
        last = item_motions[-1]
        if "adjourn" in last.heading.casefold():
            deciding = item_motions
        else:
            deciding = [m for m in item_motions if not m.procedural]
        if deciding:
            chosen.append(deciding[-1])
    return chosen


def votes_text(motions: list[MinutesMotion]) -> str:
    """The motions as lines the writer reads: item, motion, mover, seconder, tally, page."""
    lines = []
    for motion in deciding_motions(motions):
        lines.append(
            f"- {motion.item}: {motion.motion}. Moved by {motion.mover}; seconded by "
            f"{motion.seconder}. {motion.result} {motion.tally} (minutes p. {motion.page})."
        )
    return "\n".join(lines)


def votes_from_documents(data: dict[str, object]) -> str:
    """Every motion in the documents' minutes entry, or "" when they hold none."""
    documents = data.get("documents") if isinstance(data, dict) else None
    for document in documents or []:
        if not isinstance(document, dict) or document.get("kind") != "minutes":
            continue
        pages = tuple(
            str(page.get("text") or "")
            for page in document.get("pages") or []
            if isinstance(page, dict)
        )
        if pages:
            return votes_text(parse_motions(pages))
    return ""
