"""No-new-claims check: a script may only state facts its cited claims state.

Deterministic rules (these decide pass or fail):
- every line that states a fact cites at least one claim id, and every cited id exists;
- every number in a line (after spoken numbers become digits) appears in a cited claim;
- every capitalised name in a line appears in a cited claim (or is a host name or show name);
- spoken text holds no digits;
- uncited lines are allowed only when they are reactions or questions;
- the disclaimer is Theo's line 2 and appears once; the intro names the show and city; the
  outro is Theo's last line and points to the city's video place (YouTube channel by default). The intro and outro may also use
  the recap header (city, body, date) and need no claim id.

The optional model check asks the writer model, once per line, whether the cited claims support
the line. Its disagreements are reported as warnings; they do not change pass or fail.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from civiccast_brief.script.numbers import NUMBER_WORDS, number_atoms
from civiccast_brief.script.recap import DEFAULT_VIDEO_PLACE, Recap
from civiccast_brief.script.writer import DISCLAIMER, SPEAKERS, ScriptLine

MAX_LINE_WORDS = 45
_NAME_RE = re.compile(r"\b[A-Z][A-Za-z]+(?:'[A-Za-z]+)?\b")
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_DIGIT_RE = re.compile(r"\d")
_SPOKEN_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")

# Names the show itself uses. Everything else must come from a cited claim.
SHOW_NAMES = frozenset({"civiccast", "brief", "maya", "theo", "youtube", "ai"})

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
    return f"{recap.city} {recap.body} meeting, {recap.meeting_date}"


def _check_line(
    number: int, line: ScriptLine, recap: Recap, issues: list[Issue], *, frame: bool = False
) -> None:
    """Check one line. ``frame`` marks the intro and the outro: they may use the recap header
    and need no claim id."""
    if line.speaker not in SPEAKERS and not line.is_chapter:
        issues.append(Issue(number, "error", "speaker", f"unknown speaker {line.speaker!r}"))
        return
    if not line.text.strip():
        issues.append(Issue(number, "error", "empty", "line has no text"))
        return
    if line.text.strip() == DISCLAIMER:
        return
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
    for missing in sorted(number_atoms(line.text) - allowed_numbers):
        issues.append(
            Issue(number, "error", "number", f"number {missing} is not in the cited claim(s)")
        )
    allowed_words = _claim_words(cited_texts) | SHOW_NAMES
    for name in _proper_nouns(line.text):
        if name.lower() not in allowed_words:
            issues.append(
                Issue(number, "error", "name", f"name {name!r} is not in the cited claim(s)")
            )


def _check_structure(
    lines: list[ScriptLine], issues: list[Issue], video_place: str = DEFAULT_VIDEO_PLACE
) -> None:
    disclaimers = [i for i, line in enumerate(lines, 1) if line.text.strip() == DISCLAIMER]
    if len(disclaimers) != 1:
        issues.append(
            Issue(
                disclaimers[1] if len(disclaimers) > 1 else 2,
                "error",
                "disclaimer",
                f"the on-air disclaimer must appear once; found {len(disclaimers)}",
            )
        )
    elif disclaimers[0] != 2 or lines[1].speaker != "S2":
        issues.append(
            Issue(disclaimers[0], "error", "disclaimer", "the disclaimer must be Theo's line 2")
        )
    if lines:
        first = lines[0]
        if first.speaker != "S1" or "civiccast brief" not in first.text.lower():
            issues.append(
                Issue(1, "error", "intro", "line 1 must be Maya's intro naming CivicCast Brief")
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
                    f"the last line must be Theo's closing line pointing to the city's {video_place}",
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
    recap: Recap, lines: list[ScriptLine]
) -> tuple[list[ScriptLine], list[ScriptLine]]:
    """Remove lines that add a fact the recap lacks, then chapters left with no speech.

    A dropped line can only take facts out, never add them, so this is safe to run
    without a model. The intro (line 1), the disclaimer (line 2) and the outro (last
    line) are never dropped: if they fail, the script still fails and goes back to the
    writer or a human. Returns (kept lines, dropped lines).
    """
    kept = list(lines)
    dropped: list[ScriptLine] = []
    while True:
        report = check_script(recap, kept)
        protected = {1, 2, len(kept)}
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
    - an uncited reaction ("Right.") straight after a chapter mark or the disclaimer.
    The intro, the disclaimer and the outro are never orphans.
    """
    last = len(lines) - 1
    for index in range(2, last):
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
            elif index == 2 or lines[index - 1].is_chapter:
                return index
    return None


def check_script(recap: Recap, lines: list[ScriptLine]) -> CheckReport:
    """Run the deterministic rules over a script."""
    report = CheckReport(lines=list(lines))
    _check_structure(lines, report.issues, recap.video_place)
    for number, line in enumerate(lines, 1):
        frame = number in (1, len(lines)) and not line.is_chapter
        _check_line(number, line, recap, report.issues, frame=frame)
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
        if line.text.strip() == DISCLAIMER:
            continue
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
