"""No-new-claims check: a script may only state facts its cited claims state.

Deterministic rules (these decide pass or fail):
- every line that states a fact cites at least one claim id, and every cited id exists;
- every number in a line (after spoken numbers become digits) appears in a cited claim;
- every capitalised name in a line appears in a cited claim (or is a host name or show name);
- spoken text holds no digits;
- uncited lines are allowed only when they are reactions or questions;
- the intro names the show and city; the outro is Alex's last line and points to the city's
  video place (YouTube channel by default). The intro and outro may also use the recap header
  (city, body, date) and need no claim id.

The optional model check asks the writer model, once per line, whether the cited claims support
the line. Its disagreements are reported as warnings; they do not change pass or fail.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from civiccast_brief.meeting.roster import Roster
from civiccast_brief.script.numbers import NUMBER_WORDS, number_atoms
from civiccast_brief.script.recap import DEFAULT_VIDEO_PLACE, Recap, named_place
from civiccast_brief.script.writer import SPEAKERS, ScriptLine, strip_old_disclaimer

MAX_LINE_WORDS = 45
_NAME_RE = re.compile(r"\b[A-Z][A-Za-z]+(?:'[A-Za-z]+)?\b")
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_DIGIT_RE = re.compile(r"\d")
_SPOKEN_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")

# Names the show itself uses. Everything else must come from a cited claim. The old host
# names stay allowed so a saved script that still says them passes the check.
SHOW_NAMES = frozenset({"civiccast", "brief", "pat", "alex", "maya", "theo", "youtube", "ai"})

# Sentence openers that are ordinary words (articles, pronouns, reactions, imperatives), not names.
_COMMON_OPENERS = frozenset(
    {
        "a",
        "about",
        "across",
        "after",
        "against",
        "ah",
        "again",
        "all",
        "welcome",
        "each",
        "another",
        "also",
        "although",
        "and",
        "any",
        "are",
        "as",
        "at",
        "back",
        "because",
        "before",
        "both",
        "but",
        "can",
        "check",
        "consider",
        "could",
        "did",
        "do",
        "does",
        "don't",
        "done",
        "even",
        "every",
        "far",
        "find",
        "finally",
        "first",
        "follow",
        "for",
        "from",
        "get",
        "go",
        "good",
        "great",
        "had",
        "has",
        "have",
        "he",
        "he's",
        "hear",
        "her",
        "here",
        "here's",
        "his",
        "how",
        "i",
        "i'd",
        "i'm",
        "if",
        "imagine",
        "in",
        "is",
        "it",
        "it's",
        "its",
        "join",
        "just",
        "keep",
        "know",
        "last",
        "later",
        "let",
        "let's",
        "like",
        "listen",
        "look",
        "meanwhile",
        "more",
        "most",
        "much",
        "my",
        "neither",
        "next",
        "no",
        "none",
        "not",
        "note",
        "now",
        "of",
        "oh",
        "ok",
        "okay",
        "on",
        "one",
        "only",
        "or",
        "other",
        "others",
        # Plural nouns a summary line opens with (Boulder, Nov 2026: "Ideas included legal
        # advice"); at a sentence start they are capitalised, not names.
        "ideas",
        "options",
        "suggestions",
        "concerns",
        "questions",
        "members",
        "speakers",
        "residents",
        "several",
        "many",
        "few",
        "our",
        "over",
        "please",
        "read",
        "remember",
        "right",
        "same",
        "second",
        "see",
        "she",
        "she's",
        "should",
        "so",
        "some",
        "sure",
        "tell",
        "than",
        "thank",
        "thanks",
        "that",
        "that's",
        "the",
        "their",
        "them",
        "then",
        "there",
        "there's",
        "these",
        "they",
        "they're",
        "this",
        "those",
        "though",
        "three",
        "to",
        "today",
        "too",
        "tune",
        "two",
        "under",
        "until",
        "up",
        "very",
        "visit",
        "was",
        "we",
        "we're",
        "well",
        "were",
        "what",
        "what's",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "within",
        "would",
        "yeah",
        "yes",
        "you",
        "you're",
        "your",
        "watch",
    }
)

# Words that make a short line a factual claim rather than a reaction.
_FACT_WORDS = frozenset(
    {
        "vote",
        "voted",
        "votes",
        "approve",
        "approved",
        "approves",
        "reject",
        "rejected",
        "passed",
        "passes",
        "failed",
        "fails",
        "motion",
        "ordinance",
        "resolution",
        "council",
        "board",
        "member",
        "members",
        "mayor",
        "city",
        "dollar",
        "dollars",
        "percent",
        "million",
        "thousand",
        "hundred",
        "staff",
        "applicant",
        "resident",
        "residents",
        "speaker",
        "speakers",
        "comment",
        "public",
        "hearing",
        "plan",
        "said",
        "says",
        "unanimous",
        "unanimously",
        "tax",
        "taxes",
        "election",
        "annexation",
        "acres",
        "units",
        "meeting",
        "record",
        "video",
        "carried",
        "carries",
        "approval",
        "opposed",
        "support",
        "opposition",
        "study",
        "traffic",
        "variance",
        "appeal",
        "garage",
        "museum",
        "visitors",
    }
)


@dataclass(frozen=True)
class Issue:
    line: int
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class ModelVerdict:
    line: int
    states_fact: bool | None
    supported: bool | None
    problem: str


@dataclass
class CheckReport:
    lines: list[ScriptLine]
    issues: list[Issue] = field(default_factory=list)
    model_verdicts: list[ModelVerdict] | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def speech_lines(self) -> int:
        return sum(1 for line in self.lines if not line.is_chapter)

    @property
    def chapter_lines(self) -> int:
        return sum(1 for line in self.lines if line.is_chapter)

    @property
    def spoken_words(self) -> int:
        return sum(
            len(_SPOKEN_WORD_RE.findall(line.text)) for line in self.lines if not line.is_chapter
        )

    def render(self) -> str:
        warnings = len(self.issues) - len(self.errors)
        out = [
            f"CHECK RESULT: {'PASS' if self.passed else 'FAIL'}",
            (
                f"Script: {len(self.lines)} lines ({self.speech_lines} speech, "
                f"{self.chapter_lines} chapter), {self.spoken_words} spoken words"
            ),
            f"Deterministic: {len(self.errors)} error(s), {warnings} warning(s)",
        ]
        if self.model_verdicts is None:
            out.append("Model check: not run")
        else:
            disagreements = [v for v in self.model_verdicts if _model_disagrees(v)]
            out.append(
                f"Model check: {len(self.model_verdicts)} line(s) checked, "
                f"{len(disagreements)} disagreement(s) with the deterministic check"
            )
        out.append("")
        if not self.issues and not self.model_verdicts:
            out.append("No issues.")
        for issue in sorted(self.issues, key=lambda i: (i.line, i.severity)):
            out.append(
                f"  line {issue.line} [{_label(self.lines, issue.line)}] "
                f"{issue.severity.upper()} {issue.code}: {issue.message}"
            )
        for verdict in self.model_verdicts or []:
            if _model_disagrees(verdict):
                out.append(
                    f"  line {verdict.line} [{_label(self.lines, verdict.line)}] "
                    f"MODEL states_fact={verdict.states_fact} supported={verdict.supported}: "
                    f"{verdict.problem or '(no reason given)'}"
                )
        return "\n".join(out) + "\n"


def _label(lines: list[ScriptLine], number: int) -> str:
    if not 1 <= number <= len(lines):
        return "?"
    line = lines[number - 1]
    text = line.text if len(line.text) <= 70 else line.text[:67] + "..."
    return f"{line.speaker}: {text}"


_POSSESSIVE_RE = re.compile(r"'s$")
_CLOCK_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


_ONE_RE = re.compile(r"\bone\b", re.IGNORECASE)


def _clock_atoms(text: str) -> set[str]:
    """A claim's "8:59 p.m." spoken as "eight fifty-nine p.m." reads as 859."""
    return {f"{int(h)}{m}" for h, m in _CLOCK_RE.findall(text)}


def _spoken_one(text: str) -> set[str]:
    """A claim's "phase one of the project" reads as no number (an idiom before "of"), but
    the line's "phase one, with" reads as 1. The word is in the claim, so 1 is allowed."""
    return {"1"} if _ONE_RE.search(text) else set()


def _normalise(text: str) -> str:
    return text.replace("St.", "Saint")


def _proper_nouns(text: str) -> list[str]:
    cleaned = _normalise(text)
    names = []
    for match in _NAME_RE.finditer(cleaned):
        before = cleaned[: match.start()].rstrip()
        opener = not before or before[-1] in ".!?:"
        word = match.group(0)
        if word.lower() in NUMBER_WORDS:
            continue  # a number word is checked as a number, not a name
        if opener and word.lower() in _COMMON_OPENERS:
            continue
        names.append(word)
    return names


def _claim_words(texts: list[str]) -> set[str]:
    words: set[str] = set()
    for text in texts:
        words.update(_WORD_RE.findall(_normalise(text).lower()))
    return words


def _is_reaction(text: str) -> bool:
    """A short line with no number, name or fact word: "Right.", "That's a big number."."""
    if text.strip().endswith("?"):
        return True
    words = _WORD_RE.findall(text.lower())
    if len(words) > 8 or not words:
        return False
    if number_atoms(text) or _proper_nouns(text):
        return False
    return not any(word in _FACT_WORDS for word in words)


def header_text(recap: Recap) -> str:
    """The recap header: city, body and date. They come from the roster and the video title,
    not from the model, so the intro and outro may say them without citing a claim."""
    return f"{named_place(recap)} meeting, {recap.meeting_date}"


def misnamed_bodies(recap: Recap) -> list[str]:
    """A carried board named with the site's city: "Louisville Board of Education" for a
    roundup whose label says "Boulder Valley Board of Education". Empty when every body is
    the site's own (or a body's label is missing, which is reported elsewhere)."""
    if not recap.body_label:
        return []
    label = recap.body_label.casefold()
    out = []
    for body in recap.body.split(" and "):
        wrong = f"{recap.city} {body}"
        if wrong.casefold() not in label:
            out.append(wrong)
    return out


#: A claim that names one person's vote: "Schuchard voted yes.", "Council Member Adams voted no."
#: A name must be capitalised; a lowercase "council voted yes" names nobody. The side words
#: stay case-insensitive.
_INDIVIDUAL_VOTE_RE = re.compile(
    r"(?<![\w'’-])(?P<name>[A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*)*)\s+"
    r"(?:voted|votes|voting)\s+(?P<side>(?i:yes|no|aye|nay))\b"
)
#: A body is not a person: "The Council voted yes", "Commission voted no" name no voter.
#: "Council Member Adams voted no" still counts, because its last word is a surname.
_BODY_WORDS = frozenset(
    {
        "aldermen",
        "board",
        "city",
        "commission",
        "commissioner",
        "commissioners",
        "committee",
        "council",
        "county",
        "district",
        "mayor",
        "member",
        "members",
        "staff",
        "supervisors",
        "town",
        "trustees",
        "village",
    }
)
#: A vote tally in a claim: "a vote of 5 to three", "approved six to one".
_TALLY_RE = re.compile(r"\b(?P<first>\d{1,3}|[A-Za-z]+)\s+to\s+(?P<second>\d{1,3}|[A-Za-z]+)\b")
_YES_SIDE_RE = re.compile(r"\b(?:yes|aye)\b|\bin favor\b|\bsupport(?:ed|ing)?\b", re.IGNORECASE)
_NO_SIDE_RE = re.compile(r"\b(?:no|nay)\b|\bagainst\b|\bopposed\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _number_value(word: str) -> int | None:
    atoms = number_atoms(word)
    if len(atoms) == 1:
        only = next(iter(atoms))
        if only.isdigit():
            return int(only)
    return None


#: A claim is read for a tally only when it also states a vote: a rate that rose "from 3 to 5
#: percent" is a number range, not a vote count.
_VOTE_CLAIM_RE = re.compile(r"\b(?:vote|votes|voted|voting|roll[\s-]?call|motion)\b", re.IGNORECASE)
#: Only a sentence that says a vote can be partial: "Adams and Kaplan raised no objection" is
#: a statement of no opposition, not a list of no voters.
_SENTENCE_VOTE_RE = re.compile(r"\b(?:vote|votes|voted|voting)\b", re.IGNORECASE)


def _vote_tallies(texts: list[str]) -> list[tuple[int, int]]:
    """The distinct (yes, no) tallies the cited vote claims state. A failed vote reverses the
    order. More than one tally means the tallies cannot be tied to one vote."""
    tallies: list[tuple[int, int]] = []
    for text in texts:
        if not _VOTE_CLAIM_RE.search(text):
            continue
        for match in _TALLY_RE.finditer(text):
            first, second = (
                _number_value(match.group("first")),
                _number_value(match.group("second")),
            )
            if first is None or second is None:
                continue
            if re.search(
                r"\b(?:failed|fails|rejected|rejects|lost|loses|denied)\b", text, re.IGNORECASE
            ):
                tally = (second, first)
            else:
                tally = (first, second)
            if tally not in tallies:
                tallies.append(tally)
    return tallies


def _named_voters(texts: list[str]) -> dict[str, set[str]]:
    """The surnames a cited vote claim names voting yes or no."""
    voters: dict[str, set[str]] = {"yes": set(), "no": set()}
    for text in texts:
        if not _VOTE_CLAIM_RE.search(text):
            continue
        for match in _INDIVIDUAL_VOTE_RE.finditer(text):
            surname = match.group("name").split()[-1]
            if _POSSESSIVE_RE.sub("", surname.lower()) in _BODY_WORDS:
                continue  # "The Council voted yes" names a body, not a voter
            side = "yes" if match.group("side").lower() in {"yes", "aye"} else "no"
            voters[side].add(surname)
    return voters


def _partial_details(text: str, cited_texts: list[str]) -> list[tuple[str, str, int]]:
    """(sentence, side, target) for each sentence that names only some voters on one side.

    Returns nothing unless the cited vote claims support a single reading: one or no tally, and
    named voters that fit inside that tally. Two different tallies mean the claims hold more
    than one vote, so no sentence can be judged against them.
    """
    voters = _named_voters(cited_texts)
    if not voters["yes"] and not voters["no"]:
        return []
    tallies = _vote_tallies(cited_texts)
    if len(tallies) > 1:
        return []
    tally = tallies[0] if tallies else None
    if tally is not None and (len(voters["yes"]) > tally[0] or len(voters["no"]) > tally[1]):
        # The claims name more voters than the tally allows: they are not one vote.
        return []
    targets = {
        "yes": tally[0] if tally else len(voters["yes"]),
        "no": tally[1] if tally else len(voters["no"]),
    }
    out: list[tuple[str, str, int]] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not _SENTENCE_VOTE_RE.search(sentence):
            continue
        yes_here = _YES_SIDE_RE.search(sentence) is not None
        no_here = _NO_SIDE_RE.search(sentence) is not None
        if yes_here == no_here:
            # No side, or both: the names cannot be assigned to a side.
            continue
        side = "yes" if yes_here else "no"
        named = {
            surname
            for surname in voters[side]
            if re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", sentence)
        }
        if not named or len(named) >= targets[side]:
            continue
        out.append((sentence, side, targets[side]))
    return out


def _partial_vote(text: str, cited_texts: list[str]) -> list[str]:
    """Why a line names only some voters on a side of a vote, or [].

    The writer may name every voter on a side or give only the count. A line that names some
    of them, beside a tally or beside cited claims that name more, is misleading: Boulder,
    Nov 12 2026 printed "Schuchard, Speer, Benjamin and Mayor Brockett were among the yes
    votes. Adams and Kaplan voted no." when the 5 to 3 vote also had Marquis yes and Winer no.
    """
    voters = _named_voters(cited_texts)
    out: list[str] = []
    for sentence, side, target in _partial_details(text, cited_texts):
        named = sum(
            1
            for surname in voters[side]
            if re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", sentence)
        )
        out.append(
            f"names {named} of the {target} {side} voters; "
            "name every voter on that side or give only the count"
        )
    return out


_NUMBER_UNITS = (
    "zero",
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
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_NUMBER_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def _number_words(value: int) -> str:
    if 0 <= value < 20:
        return _NUMBER_UNITS[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return f"{_NUMBER_TENS[tens]}-{_NUMBER_UNITS[ones]}" if ones else _NUMBER_TENS[tens]
    return str(value)


def _count_sentence(tally: tuple[int, int] | None) -> str:
    """A count-only sentence a partial-vote line can stand in, never a name.

    The count comes from the cited claims' tally, so it adds no fact. Without a tally (or with
    one too large to spell), the sentence says only that a vote was recorded.
    """
    if tally is not None and tally[0] < 100 and tally[1] < 100:
        return f"The vote was {_number_words(tally[0])} to {_number_words(tally[1])}."
    return "The vote was recorded."


def repair_partial_votes(
    recap: Recap, lines: list[ScriptLine], roster: Roster | None = None
) -> list[ScriptLine]:
    """Reduce each sentence that names only some voters to its count, never inventing a name.

    A partial-vote error must not cost a meeting its script. This is safe to run with no model:
    it only takes names out of the offending sentence and replaces them with the cited claims'
    own tally, so the result states no fact the claims lack.
    """
    report = check_script(recap, lines, roster=roster)
    bad = {issue.line for issue in report.errors if issue.code == "partial_vote"}
    if not bad:
        return list(lines)
    known = {claim.id: claim for claim in recap.claims}
    out: list[ScriptLine] = []
    for number, line in enumerate(lines, 1):
        if number not in bad or line.is_chapter:
            out.append(line)
            continue
        cited = [known[cid].text for cid in line.claim_ids if cid in known]
        details = _partial_details(line.text, cited)
        if not details:
            out.append(line)
            continue
        tallies = _vote_tallies(cited)
        tally = tallies[0] if len(tallies) == 1 else None
        partial = {sentence for sentence, _side, _target in details}
        replacement = _count_sentence(tally)
        sentences: list[str] = []
        for sentence in _SENTENCE_SPLIT_RE.split(line.text):
            if sentence in partial:
                if not sentences or sentences[-1] != replacement:
                    sentences.append(replacement)
            else:
                sentences.append(sentence)
        out.append(ScriptLine(line.speaker, " ".join(sentences), line.claim_ids))
    return out


def _roster_name_words(roster: Roster | None, cited_texts: list[str]) -> set[str]:
    """The full name of a roster member whose unique first name a cited claim prints.

    The writer expands a bare first name ("Rob") to the record's full name ("Rob Kaplan"). The
    surname is not in the claim, but it is the record's own spelling for that one member, so it
    is allowed there and nowhere else.
    """
    if roster is None:
        return set()
    cited = _claim_words(cited_texts)
    counts: dict[str, int] = {}
    for member in roster.members:
        first = member.first.strip().lower()
        if first and " " not in first:
            counts[first] = counts.get(first, 0) + 1
    out: set[str] = set()
    for member in roster.members:
        first = member.first.strip().lower()
        if first and counts.get(first) == 1 and first in cited:
            out.update(_WORD_RE.findall(member.name.lower()))
    return out


def _check_line(
    number: int,
    line: ScriptLine,
    recap: Recap,
    issues: list[Issue],
    *,
    frame: bool = False,
    roster: Roster | None = None,
) -> None:
    """Check one line. ``frame`` marks the intro and the outro: they may use the recap header
    and need no claim id."""
    if line.speaker not in SPEAKERS and not line.is_chapter:
        issues.append(Issue(number, "error", "speaker", f"unknown speaker {line.speaker!r}"))
        return
    if not line.text.strip():
        issues.append(Issue(number, "error", "empty", "line has no text"))
        return
    for wrong in misnamed_bodies(recap):
        if re.search(rf"(?<!\w){re.escape(wrong)}(?!\w)", line.text, re.IGNORECASE):
            issues.append(
                Issue(
                    number,
                    "error",
                    "body_place",
                    f"{wrong!r} is not a board: {recap.body_label!r}",
                )
            )
    # A chapter title is shown on screen and never spoken, so it may use digits.
    if not line.is_chapter and _DIGIT_RE.search(line.text):
        issues.append(
            Issue(number, "error", "digits", "digits in spoken text; spell the number in words")
        )
    known = {c.id: c for c in recap.claims}
    unknown = [cid for cid in line.claim_ids if cid not in known]
    for cid in unknown:
        issues.append(Issue(number, "error", "unknown_claim", f"cites unknown claim {cid}"))
    cited = [known[cid] for cid in line.claim_ids if cid in known]
    if not cited and not frame:
        if not _is_reaction(line.text):
            issues.append(Issue(number, "error", "uncited", "states a fact but cites no claim id"))
        return
    cited_texts = [c.text for c in cited] + ([header_text(recap)] if frame else [])
    allowed_numbers = set()
    for text in cited_texts:
        allowed_numbers |= number_atoms(text)
    for text in cited_texts:
        allowed_numbers |= _clock_atoms(text) | _spoken_one(text)
    for missing in sorted(number_atoms(line.text) - allowed_numbers):
        issues.append(
            Issue(number, "error", "number", f"number {missing} is not in the cited claim(s)")
        )
    allowed_words = _claim_words(cited_texts) | SHOW_NAMES | _roster_name_words(roster, cited_texts)
    for name in _proper_nouns(line.text):
        word = name.lower()
        # "Boulder's" is the place "Boulder" the claim or the header names.
        if word not in allowed_words and _POSSESSIVE_RE.sub("", word) not in allowed_words:
            issues.append(
                Issue(number, "error", "name", f"name {name!r} is not in the cited claim(s)")
            )
    for message in _partial_vote(line.text, cited_texts):
        issues.append(Issue(number, "error", "partial_vote", message))


def _check_structure(
    lines: list[ScriptLine], issues: list[Issue], video_place: str = DEFAULT_VIDEO_PLACE
) -> None:
    if lines:
        first = lines[0]
        if first.speaker != "S1" or "civiccast brief" not in first.text.lower():
            issues.append(
                Issue(1, "error", "intro", "line 1 must be Pat's intro naming CivicCast Brief")
            )
        last_number = len(lines)
        last = lines[-1]
        place_word = video_place.split()[0].lower()
        if last.speaker != "S2" or place_word not in last.text.lower():
            issues.append(
                Issue(
                    last_number,
                    "error",
                    "outro",
                    f"the last line must be Alex's closing line pointing to the city's {video_place}",
                )
            )
    for number, line in enumerate(lines, 1):
        if line.is_chapter and (number == len(lines) or lines[number].is_chapter):
            issues.append(
                Issue(number, "error", "empty_chapter", "a chapter with no spoken line in it")
            )
    previous = None
    for number, line in enumerate(lines, 1):
        if line.is_chapter:
            continue
        if line.speaker == previous:
            issues.append(
                Issue(number, "warning", "same_speaker", "same speaker as the line before")
            )
        previous = line.speaker
        if len(_SPOKEN_WORD_RE.findall(line.text)) > MAX_LINE_WORDS:
            issues.append(Issue(number, "warning", "long_line", f"over {MAX_LINE_WORDS} words"))


_DROPPABLE = frozenset({"uncited", "number", "name", "digits", "unknown_claim"})


def drop_empty_chapters(lines: list[ScriptLine]) -> tuple[list[ScriptLine], list[ScriptLine]]:
    """Remove each chapter mark with no spoken line before the next chapter or the end.

    The audio cannot place such a mark (it starts at no line). A roundup is asked for a chapter
    per meeting and per item, so its writer often puts two marks together. Only marks go, so no
    spoken word changes. Returns (kept lines, dropped marks)."""
    kept: list[ScriptLine] = []
    dropped: list[ScriptLine] = []
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else None
        if line.is_chapter and (following is None or following.is_chapter):
            dropped.append(line)
        else:
            kept.append(line)
    return kept, dropped


def drop_unsupported(
    recap: Recap, lines: list[ScriptLine], roster: Roster | None = None
) -> tuple[list[ScriptLine], list[ScriptLine]]:
    """Remove lines that add a fact the recap lacks, then chapters left with no speech.

    A dropped line can only take facts out, never add them, so this is safe to run
    without a model. Before dropping, a line that names only some voters on a side is reduced
    to the count from its cited claims: a partial-vote error must not cost a meeting its
    script. The intro (line 1) and the outro (last line) are never dropped: if they fail, the
    script still fails and goes back to the writer or a human. Returns (kept lines, dropped
    lines).
    """
    repaired = repair_partial_votes(recap, lines, roster)
    kept = list(repaired)
    dropped: list[ScriptLine] = []
    while True:
        report = check_script(recap, kept, roster=roster)
        protected = {1, len(kept)}
        bad = {
            issue.line
            for issue in report.errors
            if issue.code in _DROPPABLE and issue.line not in protected
        }
        if not bad:
            break
        dropped.extend(line for n, line in enumerate(kept, 1) if n in bad)
        kept = [line for n, line in enumerate(kept, 1) if n not in bad]
    while (orphan := _orphan(kept)) is not None:
        dropped.append(kept.pop(orphan))
    return kept, dropped


def _orphan(lines: list[ScriptLine]) -> int | None:
    """The first line a drop left without its point, or None.

    - a chapter with no cited line before the next chapter or the outro;
    - an uncited question whose next line is not a cited answer (or the outro);
    - an uncited reaction ("Right.") straight after a chapter mark.
    The intro and the outro are never orphans.
    """
    last = len(lines) - 1
    for index in range(1, last):
        line = lines[index]
        following = lines[index + 1]
        if line.is_chapter:
            section = lines[index + 1 : last]
            end = next((n for n, item in enumerate(section) if item.is_chapter), len(section))
            if not any(item.claim_ids for item in section[:end]):
                return index
        elif not line.claim_ids:
            if line.text.rstrip().endswith("?"):
                # A question just before the outro hands over to it, so it stays.
                if index + 1 < last and (following.is_chapter or not following.claim_ids):
                    return index
            elif lines[index - 1].is_chapter:
                return index
    return None


def check_script(
    recap: Recap, lines: list[ScriptLine], roster: Roster | None = None
) -> CheckReport:
    """Run the deterministic rules over a script. A saved script's old on-air disclaimer line
    is dropped first, so a script written before the label was removed still checks. With a
    ``roster``, a member's full name is allowed where a cited claim prints that member's
    unique first name (the writer's first-name expansion)."""
    lines = strip_old_disclaimer(lines)
    report = CheckReport(lines=list(lines))
    _check_structure(lines, report.issues, recap.video_place)
    for number, line in enumerate(lines, 1):
        frame = number in (1, len(lines)) and not line.is_chapter
        _check_line(number, line, recap, report.issues, frame=frame, roster=roster)
    return report


_MODEL_PROMPT = """You check one line of a podcast script against the source claims.

Source claims (the only facts the script may state):
{claims}

Script line, spoken by {speaker}: "{text}"

Answer with one JSON object and nothing else:
{{"states_fact": true or false, "supported": true or false, "problem": "..."}}
- states_fact: false only when the line is a reaction, a question or a pleasantry that states no fact about the meeting.
- supported: true only when the cited claims state every fact in the line, including every number, date, name, vote and outcome. A changed number, or a name the claims do not use, makes it false.
- problem: the unsupported words in the line, or an empty string.
"""


def _parse_verdict(answer: str, number: int) -> ModelVerdict:
    start, end = answer.find("{"), answer.rfind("}")
    try:
        data = json.loads(answer[start : end + 1])
    except (ValueError, TypeError):
        return ModelVerdict(number, None, None, "model answer was not JSON")
    states = data.get("states_fact")
    supported = data.get("supported")
    return ModelVerdict(
        line=number,
        states_fact=states if isinstance(states, bool) else None,
        supported=supported if isinstance(supported, bool) else None,
        problem=str(data.get("problem", "")).strip(),
    )


def _model_disagrees(verdict: ModelVerdict) -> bool:
    """True when the model says the line is unsupported, or its answer could not be read."""
    return verdict.supported is not True


def model_check(report: CheckReport, recap: Recap, complete: Callable[[str], str]) -> CheckReport:
    """Ask the model, line by line, whether the cited claims support it. Adds the verdicts to
    the report as warnings. Lines the deterministic check already failed are still asked."""
    known = {c.id: c for c in recap.claims}
    verdicts: list[ModelVerdict] = []
    for number, line in enumerate(report.lines, 1):
        cited = [known[cid] for cid in line.claim_ids if cid in known]
        claims_text = (
            "\n".join(f"[{c.id}] {c.text}" for c in cited) if cited else "none (no claim is cited)"
        )
        prompt = _MODEL_PROMPT.format(
            claims=claims_text,
            speaker=SPEAKERS.get(line.speaker, "the chapter title"),
            text=line.text,
        )
        verdict = _parse_verdict(complete(prompt), number)
        verdicts.append(verdict)
        if _model_disagrees(verdict):
            report.issues.append(
                Issue(number, "warning", "model", verdict.problem or "model: not supported")
            )
    report.model_verdicts = verdicts
    return report
