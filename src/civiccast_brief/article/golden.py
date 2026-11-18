"""Golden-fact scoring for meeting stories: a port of TownReporter's ``golden-facts.ts``.

A fixture records what a meeting story must get right -- the motion, who moved
it, who seconded it, the recorded tally and who sat on each side -- each fact
pinned to the record (minutes page, transcript stamp) and quoted. This module
scores a story against one fixture with no database and no model: every verdict
is a pattern match on the story text, and every verdict carries the sentence
that decided it.

Verdicts
    correct              the story states the fact the way the fixture records it.
    wrong                the story contradicts the fixture: a different tally, a
                         side inverted, the wrong member named, a title that
                         belongs to someone else, or a caption spelling printed
                         as a name.
    not-stated           the story does not state the fact at all (a required
                         fact left unstated fails the story).
    masked-correct-role  "an unidentified speaker" with no title that belongs to
                         someone else: an honest gap.
    masked-wrong-role    "an unidentified speaker" *and* a title that belongs to
                         another member.

pass = no wrong, no masked-wrong-role, and no required fact left not-stated.

Known limits, as in TownReporter: the motion is scored on the fixture's
``must_mention`` phrases; side detection reads the sentence around an
opposition/support phrase; one motion per fixture.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("motion", "mover", "seconder", "result", "opposed", "in-favor")
VERDICTS = ("correct", "wrong", "not-stated", "masked-correct-role", "masked-wrong-role")


@dataclass(frozen=True)
class Evidence:
    source: str = "recording"
    quote: str = ""
    at: str = ""
    page: int | None = None
    line: int | None = None
    note: str = ""


@dataclass(frozen=True)
class RosterMember:
    name: str
    role: str = ""
    district: str | None = None
    honorific: str | None = None
    caption_aliases: tuple[str, ...] = ()
    source: str = "roster"


@dataclass(frozen=True)
class GoldenFact:
    kind: str
    required: bool = False
    fact_source: str = ""
    name_source: str | None = None
    person: str | None = None
    result: str | None = None
    tally: str | None = None
    summary: str = ""
    must_mention: tuple[str, ...] = ()
    derivation: str = ""
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class GoldenFixture:
    id: str
    title: str
    city: str
    date: str
    roster: tuple[RosterMember, ...]
    facts: tuple[GoldenFact, ...]


@dataclass(frozen=True)
class FactVerdict:
    kind: str
    subject: str
    required: bool
    verdict: str
    sentence: str | None
    reason: str


@dataclass
class GoldenScore:
    meeting: str
    fixture_facts: int
    verdicts: list[FactVerdict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in VERDICTS})
    pass_: bool = False
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StoryText:
    headline: str = ""
    dek: str = ""
    body: str = ""


def load_fixture(raw: dict[str, Any]) -> GoldenFixture:
    meeting = raw.get("meeting") or {}
    recording = meeting.get("recording") or {}
    roster_source = meeting.get("roster_source") or {}
    del recording, roster_source
    roster = []
    for entry in raw.get("roster") or []:
        roster.append(
            RosterMember(
                name=str(entry.get("name") or ""),
                role=str(entry.get("role") or ""),
                district=entry.get("district"),
                honorific=entry.get("honorific"),
                caption_aliases=tuple(str(a) for a in entry.get("caption_name_aliases") or () if a),
                source=str(entry.get("source") or "roster"),
            )
        )
    facts = []
    for entry in raw.get("facts") or []:
        facts.append(
            GoldenFact(
                kind=str(entry.get("kind") or ""),
                required=entry.get("required") is True,
                fact_source=str(entry.get("fact_source") or ""),
                name_source=entry.get("name_source"),
                person=entry.get("person"),
                result=entry.get("result"),
                tally=entry.get("tally"),
                summary=str(entry.get("summary") or ""),
                must_mention=tuple(str(p) for p in entry.get("must_mention") or () if p),
                derivation=str(entry.get("derivation") or ""),
                evidence=tuple(
                    Evidence(
                        source=str(e.get("source") or "recording"),
                        quote=str(e.get("quote") or ""),
                        at=str(e.get("at") or ""),
                        page=e.get("page") if isinstance(e.get("page"), int) else None,
                        line=e.get("line") if isinstance(e.get("line"), int) else None,
                        note=str(e.get("note") or ""),
                    )
                    for e in entry.get("evidence") or []
                ),
            )
        )
    return GoldenFixture(
        id=str(meeting.get("id") or ""),
        title=str(meeting.get("title") or ""),
        city=str(meeting.get("city") or ""),
        date=str(meeting.get("date") or ""),
        roster=tuple(roster),
        facts=tuple(facts),
    )


def load_fixture_file(path: str | Path) -> GoldenFixture:
    return load_fixture(json.loads(Path(path).read_text(encoding="utf-8")))


def parse_story_text(raw: str) -> StoryText:
    """Split a story at HEADLINE / DEK / BODY markers; text with none is body."""
    story = {"headline": "", "dek": "", "body": ""}
    buffers: dict[str, list[str]] = {"headline": [], "dek": [], "body": []}
    current: str | None = None
    saw = False
    for line in raw.replace("\r\n", "\n").split("\n"):
        marker = re.fullmatch(r"(HEADLINE|DEK|BODY)\s*", line.strip())
        if marker:
            current = marker.group(1).lower()
            saw = True
            continue
        if current:
            buffers[current].append(line)
    if not saw:
        return StoryText(body=raw.strip())
    for part, lines in buffers.items():
        story[part] = re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip())
    return StoryText(**story)


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


_ABBREVIATION = re.compile(
    r"(?:^|\s)(?:Mr|Mrs|Ms|Dr|St|Mt|No|Inc|Jr|Sr|vs|etc|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|"
    r"Sept|Oct|Nov|Dec)\.$",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+", text):
        if pieces and _ABBREVIATION.search(pieces[-1]):
            pieces[-1] = f"{pieces[-1]} {raw}"
        else:
            pieces.append(raw)
    return pieces


def _story_sentences(story: StoryText) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part, text in (("headline", story.headline), ("dek", story.dek), ("body", story.body)):
        if not text.strip():
            continue
        for line in re.split(r"\n+", text):
            for piece in _sentences(line):
                if piece.strip():
                    out.append((piece.strip(), part))
    return out


_TITLE_FORMS = (
    ("Mayor Pro Tem", r"mayor\s+pro\s+tem(?:pore)?"),
    ("Mayor", r"mayors?"),
    ("Council Member", r"council\s?members?|councilmember|councilwoman|councilman|council"),
)
_TITLE = re.compile(
    "^(?:" + "|".join(source for _, source in _TITLE_FORMS) + r")[\s,]+", re.IGNORECASE
)
_NAME_RUN = re.compile(r"^([A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*){0,2})", re.UNICODE)
_MASKED = re.compile(
    r"\b(?:an?\s+|the\s+|one\s+|another\s+|two\s+|three\s+|several\s+)?unidentified\s+"
    r"(?:speakers?|council\s?members?|officials?|people|persons?|residents?|voters?)\b|"
    r"\bunnamed\s+(?:speakers?|council\s?members?)\b|"
    r"\bwhose\s+name\s+(?:was|is)\s+not\s+(?:stated|given|verified|known)\b|"
    r"\bname\s+not\s+(?:stated|given|verified)\b|\bnot\s+named\b",
    re.IGNORECASE,
)


def _strip_titles(text: str) -> tuple[list[str], str]:
    rest = text.lstrip(" ,;:-")
    titles: list[str] = []
    while True:
        for title, source in _TITLE_FORMS:
            match = re.match(rf"^(?:{source})[\s,]+", rest, re.IGNORECASE)
            if match:
                titles.append(title)
                rest = rest[match.end() :]
                break
        else:
            break
    return titles, rest


def _title_owners(title: str, fixture: GoldenFixture) -> list[RosterMember]:
    return [m for m in fixture.roster if (m.honorific or m.role) == title]


def _title_conflict(titles: list[str], person: str | None, fixture: GoldenFixture) -> str | None:
    if not person:
        return None
    for title in titles:
        owners = _title_owners(title, fixture)
        if owners and all(owner.name != person for owner in owners):
            return title
    return None


def _match_person(printed: str, fixture: GoldenFixture) -> tuple[RosterMember | None, bool]:
    wanted = _normal(printed)
    for member in fixture.roster:
        surname = _normal(member.name.split()[-1]) if member.name.split() else ""
        if _normal(member.name) == wanted or surname == wanted:
            return member, False
    for member in fixture.roster:
        if any(_normal(alias) == wanted for alias in member.caption_aliases):
            return member, True
    return None, False


def _mention_after(window: str) -> tuple[str, str, list[str]]:
    """(kind, name, titles) for the person a window starts with."""
    trimmed = window.lstrip(" ,;:-")
    titles, rest = _strip_titles(trimmed)
    masked = _MASKED.search(trimmed)
    consumed = len(trimmed) - len(rest)
    if masked and masked.start() <= consumed:
        return "masked", "", titles
    name = _NAME_RUN.match(rest)
    if name:
        return "named", " ".join(name.group(1).split()), titles
    return "none", "", titles


_TITLE_SOURCE = "|".join(f"(?:{source})" for _, source in _TITLE_FORMS)
_TITLE_THEN_NAME = re.compile(
    rf"(?:(?:{_TITLE_SOURCE})\b[\s,]+)+([A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*){{0,2}})\s*$",
    re.IGNORECASE,
)
_TRAILING_NAME = re.compile(r"([A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*){0,2})\s*$")


def _mention_before(window: str) -> tuple[str, str, list[str]]:
    trimmed = window.rstrip(" ,;:-")
    masked = _MASKED.search(trimmed)
    if masked and not trimmed[masked.end() :].strip():
        titles, _ = _strip_titles(trimmed)
        return "masked", "", titles
    titled = _TITLE_THEN_NAME.search(trimmed)
    if titled:
        # Only the title printed immediately before this name belongs to it: in
        # "Council Member Mike Foster moved ..., and Mayor Pro Tem Jenn Kaaoush
        # seconded", the seconder is Mayor Pro Tem, not a Council Member.
        titles, _ = _strip_titles(titled.group(0))
        return "named", " ".join(titled.group(1).split()), titles
    trailing = _TRAILING_NAME.search(trimmed)
    if trailing:
        return "named", " ".join(trailing.group(1).split()), []
    return "none", "", []


def _attribution(sentence: str, kind: str) -> tuple[str, str, list[str]]:
    after = (
        (r"\b(?:was\s+|were\s+)?made\s+by\b", r"\b(?:was\s+|were\s+)?moved\s+by\b")
        if kind == "mover"
        else (r"\bseconded\s+by\b",)
    )
    before = (r"\bmoved\b",) if kind == "mover" else (r"\bseconded\b",)
    for pattern in after:
        match = re.search(pattern, sentence, re.IGNORECASE)
        if match:
            found = _mention_after(sentence[match.end() : match.end() + 160])
            if found[0] != "none":
                return found
    for pattern in before:
        match = re.search(pattern, sentence, re.IGNORECASE)
        if match:
            found = _mention_before(sentence[max(0, match.start() - 90) : match.start()])
            if found[0] != "none":
                return found
    return "none", "", []


def _score_attribution(fact: GoldenFact, story: StoryText, fixture: GoldenFixture) -> FactVerdict:
    person = fact.person
    base = {"kind": fact.kind, "subject": person or "(unnamed)", "required": fact.required}
    # The phrases that show a sentence is about this fixture's motion come from the motion
    # fact, not from the attribution fact (whose own must_mention list is empty).
    motion_phrases = list(
        dict.fromkeys(_normal(phrase) for other in fixture.facts for phrase in other.must_mention)
    )

    def about_the_motion(text: str) -> bool:
        folded = _normal(text)
        return bool(motion_phrases) and any(phrase in folded for phrase in motion_phrases)

    def verdict_for(text: str, kind: str, name: str, titles: list[str]) -> FactVerdict:
        if kind == "masked":
            conflict = _title_conflict(titles, person, fixture)
            if conflict:
                owner = " / ".join(m.name for m in _title_owners(conflict, fixture))
                return FactVerdict(
                    **base,
                    verdict="masked-wrong-role",
                    sentence=text,
                    reason=f'left the {fact.kind} unidentified but attached "{conflict}", which belongs to {owner}',
                )
            return FactVerdict(
                **base,
                verdict="masked-correct-role",
                sentence=text,
                reason=f"left the {fact.kind} unidentified with no title claimed for another member",
            )
        member, alias = _match_person(name, fixture)
        if member is None:
            return FactVerdict(
                **base,
                verdict="wrong",
                sentence=text,
                reason=f"named {name} as the {fact.kind}, who is not on the roster",
            )
        if member.name != person:
            return FactVerdict(
                **base,
                verdict="wrong",
                sentence=text,
                reason=(
                    f"named {name} (a caption spelling of {member.name}) as the {fact.kind}; it was {person}"
                    if alias
                    else f"named {member.name} as the {fact.kind}; it was {person}"
                ),
            )
        if alias:
            return FactVerdict(
                **base,
                verdict="wrong",
                sentence=text,
                reason=f'printed the caption spelling "{name}" for {person}; the roster spells it {person}',
            )
        conflict = _title_conflict(titles, person, fixture)
        if conflict:
            owner = " / ".join(m.name for m in _title_owners(conflict, fixture))
            return FactVerdict(
                **base,
                verdict="wrong",
                sentence=text,
                reason=f'gave {person} the title "{conflict}", which belongs to {owner}',
            )
        return FactVerdict(
            **base, verdict="correct", sentence=text, reason=f"named {person} as the {fact.kind}"
        )

    sentences = _story_sentences(story)
    elsewhere: FactVerdict | None = None
    for index, (text, _part) in enumerate(sentences):
        kind, name, titles = _attribution(text, fact.kind)
        if kind == "none":
            continue
        found = verdict_for(text, kind, name, titles)
        # The motion and its mover can be in neighbouring sentences ("The council adopted
        # the fire code 6-0. Council Member Mike Foster moved the ordinance..."), so the
        # sentence's own words and the sentences around it decide whether it is this motion.
        nearby = " ".join(part for part, _ in sentences[max(0, index - 1) : index + 1])
        if about_the_motion(nearby):
            return found
        # A story about a whole meeting names the mover and seconder of other motions too.
        # Those sentences are not this motion's; keep one in case no sentence is about it.
        if elsewhere is None:
            elsewhere = found
    if elsewhere is not None:
        return elsewhere
    return FactVerdict(
        **base,
        verdict="not-stated",
        sentence=None,
        reason=f"the story never says who {'moved' if fact.kind == 'mover' else 'seconded'} the motion",
    )


_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}
_TALLY = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*"
    r"(?:-|–|—|to)\s*(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
    re.IGNORECASE,
)
_VOTE_VERB = re.compile(
    r"\b(?:carr(?:ied|ies|y)|pass(?:ed|es)|approv(?:ed|es)|adopt(?:ed|s)|fail(?:ed|s)?|"
    r"reject(?:ed|s)?|defeat(?:ed|s)?|vot(?:e|ed|es|ing)|tally|in opposition|unanimous)\b",
    re.IGNORECASE,
)
_VOTE_FAILED = re.compile(
    r"\b(?:fail(?:ed|s)?|reject(?:ed|s)?|defeat(?:ed|s)?|did\s+not\s+(?:carry|pass)|"
    r"not\s+carried|unanimous(?:ly)?)\b",
    re.IGNORECASE,
)
_MONTH_BEFORE = re.compile(
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*$", re.IGNORECASE
)


def _normal_tally(text: str) -> str:
    parts = re.split(r"\s*(?:-|–|—|to)\s*", text.strip().lower())
    left, right = (parts + ["", ""])[:2]
    return f"{_NUMBER_WORDS.get(left, left)}-{_NUMBER_WORDS.get(right, right)}"


def _score_result(fact: GoldenFact, story: StoryText) -> FactVerdict:
    base = {"kind": fact.kind, "subject": fact.tally or "(tally)", "required": fact.required}
    wanted = _normal_tally(fact.tally) if fact.tally else None
    for text, _part in _story_sentences(story):
        if not _VOTE_VERB.search(text):
            continue
        match = _TALLY.search(text)
        unanimous = re.search(r"\bunanimous(?:ly)?\b", text, re.IGNORECASE)
        if not match and not unanimous:
            continue
        if match and _MONTH_BEFORE.search(text[: match.start()]):
            continue
        said = _normal_tally(match.group(0)) if match else "unanimous"
        if wanted and said != wanted:
            return FactVerdict(
                **base,
                verdict="wrong",
                sentence=text,
                reason=f"reported the vote as {said}; the record records {fact.tally}",
            )
        failed = bool(_VOTE_FAILED.search(text))
        if fact.result == "carried" and failed:
            return FactVerdict(
                **base,
                verdict="wrong",
                sentence=text,
                reason=f"reported the motion as failed; the record records it carried {fact.tally}",
            )
        if fact.result == "failed" and not failed:
            return FactVerdict(
                **base,
                verdict="wrong",
                sentence=text,
                reason="reported the motion as carried; the record records it failed",
            )
        return FactVerdict(
            **base,
            verdict="correct",
            sentence=text,
            reason=f"states the {fact.tally} {fact.result or 'result'}",
        )
    return FactVerdict(
        **base,
        verdict="not-stated",
        sentence=None,
        reason=f"the story never states the {fact.tally or ''} tally".strip(),
    )


_SIDES = (
    (
        "opposed",
        r"in opposition|opposed|opposing|voted no|voting no|votes no|cast no|dissented|dissent|against",
    ),
    (
        "in-favor",
        r"in favor|in support|voted yes|voting yes|votes yes|supported|supporting|backed|favor(?:ed|ing)|favour(?:ed|ing)",
    ),
)
_SCOPE_BREAK = re.compile(
    r"\b(?:while|whereas|although|though|but|who|which|that)\b", re.IGNORECASE
)


def _mentions(text: str, fixture: GoldenFixture) -> list[tuple[RosterMember, bool]]:
    found: list[tuple[int, RosterMember, bool]] = []
    for member in fixture.roster:
        surname = member.name.split()[-1] if member.name.split() else member.name
        for form in dict.fromkeys([member.name, surname]):
            if not form:
                continue
            for match in re.finditer(rf"(?<![\w]){re.escape(form)}(?![\w])", text, re.IGNORECASE):
                found.append((match.start(), member, False))
        for alias in member.caption_aliases:
            for match in re.finditer(rf"(?<![\w]){re.escape(alias)}(?![\w])", text, re.IGNORECASE):
                found.append((match.start(), member, True))
    found.sort(key=lambda row: row[0])
    out: list[tuple[RosterMember, bool]] = []
    seen: set[str] = set()
    for _at, member, alias in found:
        if member.name not in seen:
            seen.add(member.name)
            out.append((member, alias))
    return out


def _side_assignments(story: StoryText, fixture: GoldenFixture) -> dict[str, tuple[str, bool, str]]:
    assigned: dict[str, tuple[str, bool, str]] = {}
    for text, _part in _story_sentences(story):
        occurrences = []
        for side, source in _SIDES:
            for match in re.finditer(source, text, re.IGNORECASE):
                occurrences.append((match.start(), match.end(), side))
        occurrences.sort()
        cursor = 0
        for index, (start, end, side) in enumerate(occurrences):
            trailing = re.match(r"\s+by\b", text[end:], re.IGNORECASE)
            if trailing:
                begin = end + trailing.end()
                scope = _SCOPE_BREAK.search(text[begin:])
                scope_end = begin + scope.start() if scope else len(text)
                next_start = (
                    occurrences[index + 1][0] if index + 1 < len(occurrences) else len(text)
                )
                stop = min(scope_end, next_start)
            else:
                begin, stop = cursor, start
            cursor = stop
            for member, alias in _mentions(text[begin:stop], fixture):
                assigned.setdefault(member.name, (side, alias, text))
    return assigned


def _score_side(fact: GoldenFact, assigned: dict[str, tuple[str, bool, str]]) -> FactVerdict:
    base = {"kind": fact.kind, "subject": fact.person or "(unnamed)", "required": fact.required}
    found = assigned.get(fact.person or "")
    if found is None:
        return FactVerdict(
            **base,
            verdict="not-stated",
            sentence=None,
            reason=f"the story does not place {fact.person} on the {'opposing' if fact.kind == 'opposed' else 'supporting'} side",
        )
    side, alias, sentence = found
    if side != fact.kind:
        return FactVerdict(
            **base,
            verdict="wrong",
            sentence=sentence,
            reason=f"placed {fact.person} with the {'opposing' if side == 'opposed' else 'supporting'} side; the record records {fact.kind}",
        )
    if alias:
        return FactVerdict(
            **base,
            verdict="wrong",
            sentence=sentence,
            reason=f"named {fact.person} with a caption spelling; the roster spells it {fact.person}",
        )
    return FactVerdict(
        **base,
        verdict="correct",
        sentence=sentence,
        reason=f"places {fact.person} on the {'opposing' if fact.kind == 'opposed' else 'supporting'} side",
    )


def _score_motion(fact: GoldenFact, story: StoryText) -> FactVerdict:
    base = {"kind": fact.kind, "subject": fact.summary or "(motion)", "required": fact.required}
    sentences = _story_sentences(story)
    missing = [
        phrase
        for phrase in fact.must_mention
        if not any(_normal(text).find(_normal(phrase)) >= 0 for text, _ in sentences)
    ]
    deciding = next(
        (
            text
            for text, _ in sentences
            if any(_normal(text).find(_normal(p)) >= 0 for p in fact.must_mention)
        ),
        None,
    )
    if missing:
        return FactVerdict(
            **base,
            verdict="not-stated",
            sentence=deciding,
            reason="the story does not state the motion's direction (missing: "
            + ", ".join(missing)
            + ")",
        )
    return FactVerdict(
        **base,
        verdict="correct",
        sentence=deciding,
        reason="states the motion's direction",
    )


def score(story: StoryText, fixture: GoldenFixture) -> GoldenScore:
    """Every fact scored, with the same pass rule as TownReporter."""
    assigned = _side_assignments(story, fixture)
    score_ = GoldenScore(meeting=fixture.id, fixture_facts=len(fixture.facts))
    for fact in fixture.facts:
        if fact.kind in ("mover", "seconder"):
            verdict = _score_attribution(fact, story, fixture)
        elif fact.kind == "result":
            verdict = _score_result(fact, story)
        elif fact.kind in ("opposed", "in-favor"):
            verdict = _score_side(fact, assigned)
        else:
            verdict = _score_motion(fact, story)
        score_.verdicts.append(verdict)
        score_.counts[verdict.verdict] += 1
        fatal = verdict.verdict in ("wrong", "masked-wrong-role") or (
            verdict.required and verdict.verdict == "not-stated"
        )
        if fatal:
            label = f" ({verdict.subject})" if verdict.kind not in ("motion", "result") else ""
            score_.failures.append(f"{verdict.kind}{label}: {verdict.verdict} — {verdict.reason}")
    score_.pass_ = not score_.failures
    return score_


def format_verdicts(score_: GoldenScore) -> str:
    """One line per fact, for the CLI and the proof report."""
    rows = [
        [
            v.verdict,
            v.kind,
            "yes" if v.required else "no",
            v.subject if len(v.subject) <= 24 else v.subject[:23] + "…",
            ('"' + (v.sentence if len(v.sentence) <= 96 else v.sentence[:95] + "…") + '"')
            if v.sentence
            else "—",
        ]
        for v in score_.verdicts
    ]
    header = ["VERDICT", "FACT", "REQ", "SUBJECT", "DECIDED BY"]
    widths = [max(len(header[i]), *(len(row[i]) for row in rows)) for i in range(5)]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(header)).rstrip()]
    lines += [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]
    return "\n".join(lines)
