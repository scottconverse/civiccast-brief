"""Run Scott's report-local-news skill as the meeting writer, the TownReporter way.

TownReporter writes a meeting story by running one agent in its own folder: the
skill's ``SKILL.md`` is copied in, the whole meeting is typed out as
``transcript.txt`` with a time stamp on every caption, and the agent writes
``story.txt``. This module is the Python half of that design
(``meeting-skill-writer.ts``): the folder, the transcript, the assignment, and
the split of the agent's answer into the part a reader sees and the part only an
editor sees.

Differences from TownReporter, all because CivicCast Brief already holds the
record: the agenda, packet and minutes text are written into the run folder
(``documents.txt``) instead of the agent fetching them, and the official votes
the minutes record are written beside them (``minutes-votes.txt``). The
assignment therefore forbids a web search outright.

The skill is vendored at the repository root (``report-local-news/``) with its
version and file hashes pinned in ``PIN.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from civiccast_brief.models import ModelCallError, child_env
from civiccast_brief.script.recap import Recap, seconds_of

#: The files a usable skill folder must hold.
REQUIRED_FILES = ("SKILL.md", "scripts/audit.py")
#: The marker that ends the publish block.
EDITOR_FILE_MARKER = "EDITOR FILE: NOT FOR PUBLICATION"
CLAIMS_HEADING = "CLAIMS AND SOURCES"
#: One agent run may not take longer than this (TownReporter D200: 45 minutes).
RUN_TIMEOUT_S = 45 * 60


def skill_dir(env: Mapping[str, str] | None = None) -> Path:
    """The vendored skill: ``$CIVICCAST_REPORT_LOCAL_NEWS_DIR``, else the repo's copy."""
    configured = (env or os.environ).get("CIVICAST_REPORT_LOCAL_NEWS_DIR", "").strip()
    if configured:
        return Path(configured)
    # src/civiccast_brief/article/skill.py -> the repository root.
    return Path(__file__).resolve().parents[3] / "report-local-news"


def skill_missing(directory: Path | None = None) -> list[str]:
    base = directory or skill_dir()
    return [name for name in REQUIRED_FILES if not (base / name).is_file()]


def _versions(text: str) -> list[str]:
    found = re.findall(r"^REPORT LOCAL NEWS,\s*v(\d+(?:\.\d+)*)", text, re.MULTILINE)
    found += [
        m.group(1)
        for m in re.finditer(r"^v(\d+(?:\.\d+)*)\s+(?:changes|adds)\b", text, re.MULTILINE)
    ]
    return found


def _compare(a: str, b: str) -> int:
    left = [int(part) for part in a.split(".")]
    right = [int(part) for part in b.split(".")]
    for index in range(max(len(left), len(right))):
        diff = (left[index] if index < len(left) else 0) - (
            right[index] if index < len(right) else 0
        )
        if diff:
            return diff
    return 0


def skill_version(directory: Path | None = None) -> str:
    """The highest version the vendored SKILL.md declares ("3.1"), or ""."""
    path = (directory or skill_dir()) / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    found = _versions(text)
    return max(found, key=lambda v: [int(p) for p in v.split(".")]) if found else ""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def skill_pin_ok(directory: Path | None = None) -> bool:
    """True when the vendored files are the ones PIN.json lists."""
    base = directory or skill_dir()
    try:
        pin = json.loads((base / "PIN.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    files = pin.get("files") or {}
    return all(
        (base / name).is_file() and _sha256(base / name) == digest for name, digest in files.items()
    )


def meeting_stamp(seconds: float) -> str:
    """``5:36:07`` -- hours unpadded, minutes and seconds two digits."""
    whole = max(0, int(seconds))
    return f"{whole // 3600}:{(whole % 3600) // 60:02d}:{whole % 60:02d}"


@dataclass(frozen=True)
class TranscriptLine:
    start: float
    text: str


def format_transcript(title: str, date: str, media: str, segments: Sequence[TranscriptLine]) -> str:
    """One header line naming the meeting, then one stamped line per caption."""
    lines = [f"{meeting_stamp(s.start)}  {' '.join(s.text.split())}" for s in segments]
    first = meeting_stamp(segments[0].start) if segments else "0:00:00"
    last = meeting_stamp(segments[-1].start) if segments else "0:00:00"
    named = title.strip()
    dated = named if (not date.strip() or date.strip() in named) else f"{named} - {date.strip()}"
    where = f" Media {media}." if media else ""
    return "\n".join(
        [
            f"{dated}.{where} Full captions, {len(lines)} lines, {first} to {last}.",
            "",
            *lines,
            "",
        ]
    )


def assignment_prompt(input: dict[str, str]) -> str:
    """The assignment handed to the agent. The skill is the only rule set."""
    return "\n".join(
        [
            (
                "Your instructions are the file SKILL.md in your working folder. Read ALL of it "
                "first, every line, then follow it exactly. It is the only rule set for this job. "
                f"Today is {input['today']} (America/Denver)."
            ),
            "",
            "## The assignment (what the editor's desk hands you)",
            (
                f"- Cover the whole meeting, comprehensively: {input['title']}, {input['date']}. "
                "Meeting mode."
            ),
            (
                "- The full caption transcript is transcript.txt in your working folder "
                f"({input['line_count']} lines, {input['first_stamp']} to {input['last_stamp']}). "
                "Read all of it, start to end."
            ),
            (
                "- The meeting's own records are in this folder: documents.txt (the agenda, the "
                "packet and the minutes text, page by page) and minutes-votes.txt (every vote the "
                "official minutes record, with its mover, seconder, tally and page). roster.txt "
                "gives every member's name and role in the record's spelling."
            ),
            (
                "- EDITOR SCOPE: SUPPLIED MATERIAL ONLY. Do not run any web search and do not "
                "fetch any other web page, record, packet or outlet; this overrides any research "
                "step in SKILL.md. Everything you may use is in this folder. Write only what the "
                "supplied material supports, and say in the EDITOR FILE what it does not show."
            ),
            (
                "- The audit script is at scripts/audit.py. Run it with: python scripts/audit.py "
                "story.txt --meeting"
            ),
            "",
            "## Output",
            (
                "- Write the full deliverable (both blocks, exactly as the skill lays them out) "
                "to story.txt in your working folder."
            ),
            "- Write the script's final run output to audit-final.txt.",
            (
                "- Write CHECK-FIRST.txt: AT MOST 3 lines, the items an editor must check before "
                "publishing that would do the most harm if wrong, most harmful first. Plain "
                "sentences, one per line. No bullets or numbering. If nothing rises to that "
                "level, write an empty file."
            ),
            (
                "- Write RUN-NOTES.md: which records you opened, what you could not get and why, "
                "the final script result line, and your ranking record line. Last line: RUN-DONE."
            ),
            "",
            "## Limits",
            "- Work only inside your working folder. Do not search the disk.",
            (
                "- Never open or print any .env file, any file with .private in its name, "
                "session.json or auth.json."
            ),
            "- No outreach: send no email or message to anyone. No posting anywhere.",
            (
                "- No questions: nobody can answer. If something is unclear, choose the reading "
                "the skill supports, note it in the EDITOR FILE, and keep going."
            ),
            "",
        ]
    )


def documents_text(data: dict[str, Any]) -> str:
    """Every page of ``documents.json``, labelled so a citation can name it."""
    blocks: list[str] = []
    for document in data.get("documents") or []:
        kind = str(document.get("kind") or "document")
        url = str(document.get("url") or "")
        for page in document.get("pages") or []:
            text = str(page.get("text") or "").strip()
            if not text:
                continue
            blocks.append(f"=== {kind}, page {page.get('page')} ===\nSource: {url}\n{text}\n")
    return "\n".join(blocks)


def roster_text(roster: Any, path: str = "") -> str:
    """The members' names, roles and the record's spelling, one per line."""
    members = getattr(roster, "members", None) or []
    lines = [f"Roster: {path}" if path else "Roster"]
    for member in members:
        role = getattr(member, "role", "") or ""
        district = getattr(member, "district", "") or ""
        extra = f" ({district})" if district else ""
        lines.append(f"- {member.name} — {role}{extra}")
    return "\n".join(lines) + "\n"


#: The agent runner. Tests pass a stand-in; the real one spawns the CLI.
AgentRunner = Callable[[str, Path, str, int], tuple[int, str, str]]


def claude_agent(prompt: str, work_dir: Path, model: str, timeout_s: int) -> tuple[int, str, str]:
    """``claude -p`` with tools, in the run folder, on the owner's subscription.

    The child gets no ``ANTHROPIC_API_KEY`` or ``ANTHROPIC_AUTH_TOKEN``, so it can
    never bill an API key. Permission bypass is explicit, as TownReporter's agent
    run does; the folder is the only place the assignment lets it work.
    """
    program = shutil.which("claude")
    if program is None:
        raise ModelCallError("'claude' is not on PATH; install Claude Code and sign in")
    args = [
        program,
        "-p",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--settings",
        json.dumps(
            {
                "claudeMdExcludes": ["**"],
                "disableAllHooks": True,
                "autoMemoryEnabled": False,
                "outputStyle": "default",
            }
        ),
        "--exclude-dynamic-system-prompt-sections",
        "--model",
        model,
        "--output-format",
        "json",
    ]
    done = subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(),
        cwd=str(work_dir),
        timeout=timeout_s,
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


@dataclass(frozen=True)
class SkillStory:
    headline: str
    dek: str
    body: str
    claims: str
    editor_file: str


class SkillStoryError(ValueError):
    """The agent did not write a deliverable in the skill's shape."""


def parse_skill_story(text: str) -> SkillStory:
    """Split ``story.txt`` at the EDITOR FILE marker and cut out CLAIMS AND SOURCES.

    The publish block is the headline, the dek and the body. The claims block is
    the editor's checking tool, not prose (TownReporter D217), so it is returned
    separately and kept off the public page.
    """
    lines = str(text or "").splitlines()
    marker = next((i for i, line in enumerate(lines) if line.strip() == EDITOR_FILE_MARKER), -1)
    if marker == -1:
        raise SkillStoryError(
            f'the writer did not separate the story from the editor file (no "{EDITOR_FILE_MARKER}" line)'
        )
    publish = lines[:marker]
    filled = [(i, line.strip()) for i, line in enumerate(publish) if line.strip()]
    if not filled:
        raise SkillStoryError("the writer produced no headline")
    headline = filled[0][1]
    dek = filled[1][1] if len(filled) > 1 else ""
    body_start = filled[1][0] if len(filled) > 1 else filled[0][0]
    claims_at = next(
        (i for i, line in enumerate(publish) if line.strip().upper() == CLAIMS_HEADING), -1
    )
    body_end = len(publish)
    if claims_at != -1:
        body_end = claims_at
        for index in range(claims_at - 1, -1, -1):
            line = publish[index].strip()
            if not line:
                continue
            if line == "---":
                body_end = index
            break
    body = "\n".join(publish[body_start + 1 : body_end]).strip()
    if not body:
        raise SkillStoryError("the writer produced a headline but no story")
    return SkillStory(
        headline=headline,
        dek=dek,
        body=body,
        claims="\n".join(publish[claims_at:]).strip() if claims_at != -1 else "",
        editor_file="\n".join(lines[marker:]).strip(),
    )


@dataclass(frozen=True)
class ClaimEntry:
    claim: str
    sources: tuple[str, ...]

    @property
    def timestamp(self) -> str:
        """The first ``h:mm:ss`` a source line prints, or ``0:00:00``."""
        for source in self.sources:
            match = re.search(r"\b(\d{1,2}:\d{2}:\d{2})\b", source)
            if match:
                return match.group(1)
        return "0:00:00"


def parse_claims(block: str) -> list[ClaimEntry]:
    """The Claim/Source entries of a CLAIMS AND SOURCES block, in story order."""
    entries: list[ClaimEntry] = []
    claim = ""
    sources: list[str] = []
    for line in str(block or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("claim:"):
            if claim:
                entries.append(ClaimEntry(claim, tuple(sources)))
            claim = stripped.split(":", 1)[1].strip()
            sources = []
        elif stripped.lower().startswith("source") and claim:
            sources.append(stripped)
    if claim:
        entries.append(ClaimEntry(claim, tuple(sources)))
    return entries


# --------------------------------------------------------------------------------------
# Mapping the skill's prose onto the recap's checked claims
# --------------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9$%]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "but",
        "with",
        "from",
        "by",
        "as",
        "at",
        "is",
        "was",
        "were",
        "be",
        "been",
        "are",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "he",
        "she",
        "they",
        "them",
        "their",
        "there",
        "here",
        "which",
        "who",
        "whom",
        "whose",
        "said",
        "says",
        "say",
        "would",
        "could",
        "should",
        "will",
        "shall",
        "may",
        "might",
        "can",
        "did",
        "do",
        "does",
        "have",
        "has",
        "had",
        "not",
        "no",
        "than",
        "then",
        "so",
        "such",
        "also",
        "about",
        "over",
        "under",
        "into",
        "onto",
        "more",
        "most",
        "other",
        "another",
    ]
)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOP}


_ALSO_LINE = re.compile(r"ALSO AT THE MEETING", re.IGNORECASE)


def _paragraphs(body: str) -> list[str]:
    """The body's paragraphs; the ``ALSO AT THE MEETING`` line is a heading, not a paragraph."""
    blocks = (block.strip() for block in re.split(r"\n\s*\n", body))
    return [block for block in blocks if block and not _ALSO_LINE.fullmatch(block)]


def _split_sections(body: str) -> list[tuple[str | None, list[str]]]:
    """The body as (heading, paragraphs), splitting at ``ALSO AT THE MEETING``."""
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", body):
        text = block.strip()
        if not text:
            continue
        if _ALSO_LINE.fullmatch(text):
            if paragraphs:
                sections.append((heading, paragraphs))
            heading, paragraphs = "Also at the meeting", []
            continue
        paragraphs.append(" ".join(text.split()))
    if paragraphs:
        sections.append((heading, paragraphs))
    return sections


def _claim_paragraphs(
    story: SkillStory, recap: Recap, segments: Sequence[Any], pages: dict[str, Any] | None = None
) -> tuple[list[Any], list[Any]]:
    """The paragraphs, each citing the skill claim it belongs to, and the skill claims.

    The skill's CLAIMS AND SOURCES is the evidence for its own prose: the Claim
    line is written in the story's own words, so the paragraph a claim belongs to
    is the one it shares the most words with. Every skill claim becomes a claim of
    the story's own recap (ids ``s01``...), with its time stamp, so the desk's
    existing check runs on the evidence the story prints. The document pages a
    Source line names are cited too ("packet ... pages 109 to 111" -> the packet's
    page 109 to 111). A paragraph with no claim is dropped.
    """
    from civiccast_brief.article.writer import Paragraph
    from civiccast_brief.script.recap import Claim

    entries = parse_claims(story.claims)
    paragraphs = _paragraphs(story.body)
    paragraph_words = [_words(p) for p in paragraphs]
    claims = [
        Claim(
            id=f"s{number:02d}",
            topic="the story's own claim",
            text=entry.claim,
            timestamp=entry.timestamp,
            quote=entry.claim,
        )
        for number, entry in enumerate(entries, start=1)
    ]
    assigned: dict[int, list[str]] = {}
    page_ids: dict[int, list[str]] = {}
    used: set[int] = set()
    for number, entry in enumerate(entries):
        words = _words(entry.claim)
        best, score = -1, 0.0
        for index, words_in_paragraph in enumerate(paragraph_words):
            if not words:
                continue
            overlap = len(words & words_in_paragraph) / len(words)
            bonus = 0.05 if index not in used else 0.0
            if overlap + bonus > score:
                best, score = index, overlap + bonus
        if best == -1 or score < 0.34:
            best = _nearest_paragraph(entry.timestamp, paragraphs, segments, used)
        if best == -1:
            best = next((i for i in range(len(paragraphs)) if i not in used), -1)
        if best == -1:
            continue
        used.add(best)
        assigned.setdefault(best, []).append(claims[number].id)
        page_ids.setdefault(best, []).extend(_source_pages(entry.sources, pages))
    result = []
    for index, text in enumerate(paragraphs):
        words = paragraph_words[index]
        ids = list(dict.fromkeys(assigned.get(index, ())))
        # A paragraph can carry a fact from a claim the one-to-one assignment gave to a
        # neighbouring paragraph. Any claim the paragraph shares half its words with is
        # cited too, so the paragraph's names and numbers have their evidence.
        extra = [
            claim.id
            for claim in claims
            if claim.id not in ids
            and words
            and len(words & _words(claim.text)) / len(words) >= 0.45
        ]
        ids += extra[:4]
        # The skill writes each Claim line as a sentence of the story, so a paragraph that
        # holds a claim's words holds that claim: it cites it and the pages its Source names.
        for number, claim in enumerate(claims):
            claim_words = _words(claim.text)
            if claim.id in ids or not claim_words:
                continue
            if len(words & claim_words) / len(claim_words) >= 0.9:
                ids.append(claim.id)
                page_ids.setdefault(index, []).extend(_source_pages(entries[number].sources, pages))
        if not ids and claims:
            # Every paragraph a reader sees must rest on a claim. A paragraph the
            # assignment left out takes the claim it shares the most words with.
            best = max(
                range(len(claims)),
                key=lambda n: len(words & _words(claims[n].text)) / max(1, len(words)),
            )
            ids = [claims[best].id]
        result.append(
            Paragraph(
                text=text,
                claim_ids=tuple(ids),
                page_ids=tuple(dict.fromkeys(page_ids.get(index, ()))),
            )
        )
    return result, claims


_KIND_WORD = re.compile(r"\b(packet|minutes|agenda)\b", re.IGNORECASE)
_PAGES_REF = re.compile(r"\bpages?\s+(\d+)(?:\s*(?:to|through|and|-|–)\s*(\d+))?", re.IGNORECASE)


def _source_pages(sources: Sequence[str], pages: dict[str, Any] | None) -> tuple[str, ...]:
    """The document page ids a skill Source line names: "packet ... pages 109 to 111"."""
    if not pages:
        return ()
    found: list[str] = []
    for source in sources:
        kinds = {match.group(1).lower() for match in _KIND_WORD.finditer(source)}
        if not kinds:
            continue
        # "Town of Superior agenda packet ... council minutes, page 24": the page numbers
        # count the packet, the most specific document the line names.
        kind = next(k for k in ("packet", "minutes", "agenda") if k in kinds)
        wanted = [pid for pid, page in pages.items() if str(page.kind).lower() == kind]
        for match in _PAGES_REF.finditer(source):
            first = int(match.group(1))
            last = int(match.group(2) or first)
            # A source can name a long range ("pages 112 to 118"); keep the pages the
            # documents actually hold.
            for number in range(first, min(last, first + 30) + 1):
                for pid in wanted:
                    if pages[pid].page == number:
                        found.append(pid)
    return tuple(dict.fromkeys(found))


def _nearest_recap_claim(timestamp: str, recap: Recap) -> str | None:
    try:
        at = seconds_of(timestamp)
    except (ValueError, TypeError):
        return None
    best: tuple[int, str] | None = None
    for claim in recap.claims:
        try:
            gap = abs(seconds_of(claim.timestamp) - at)
        except (ValueError, TypeError):
            continue
        if best is None or gap < best[0]:
            best = (gap, claim.id)
    return best[1] if best else None


def _nearest_paragraph(
    timestamp: str, paragraphs: list[str], segments: Sequence[Any], used: set[int]
) -> int:
    try:
        at = seconds_of(timestamp)
    except (ValueError, TypeError):
        return -1
    if not segments:
        return -1
    caption = " ".join(
        str(getattr(s, "text", ""))
        for s in segments
        if at - 20 <= float(getattr(s, "start", 0)) <= at + 60
    )
    words = _words(caption)
    best, score = -1, 0.0
    for index, text in enumerate(paragraphs):
        if index in used or not words:
            continue
        overlap = len(words & _words(text)) / len(words)
        if overlap > score:
            best, score = index, overlap
    return best if score >= 0.2 else -1


def _best_recap_claims(text: str, recap: Recap, limit: int = 3) -> tuple[str, ...]:
    words = _words(text)
    if not words:
        return ()
    scored: list[tuple[float, str]] = []
    for claim in recap.claims:
        overlap = len(words & _words(claim.text)) / max(1, len(words))
        if overlap >= 0.18:
            scored.append((overlap, claim.id))
    scored.sort(reverse=True)
    return tuple(claim_id for _, claim_id in scored[:limit])


def article_from_skill(
    story: SkillStory,
    recap: Recap,
    segments: Sequence[Any],
    pages: dict[str, Any] | None = None,
) -> tuple[Any, list[Any]]:
    """The skill's story in the site's Article shape, and its claims as recap claims."""
    from civiccast_brief.article.writer import Article, Section

    flat, claims = _claim_paragraphs(story, recap, segments, pages)
    sections_raw = _split_sections(story.body)
    # _claim_paragraphs walks the same paragraphs in the same order.
    position = 0
    sections = []
    for heading, texts in sections_raw:
        group = flat[position : position + len(texts)]
        position += len(texts)
        sections.append(Section(heading=heading, paragraphs=tuple(group)))
    article = Article(
        headline=" ".join(story.headline.split()),
        dek=" ".join(story.dek.split()),
        sections=tuple(sections),
    )
    return article, claims


# --------------------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillRun:
    ok: bool
    work_dir: Path
    story: SkillStory | None = None
    error: str = ""
    seconds: float = 0.0
    version: str = ""


def run_skill(
    *,
    folder: Path,
    title: str,
    date: str,
    media: str,
    segments: Sequence[TranscriptLine],
    documents: str,
    minutes_votes: str,
    roster: str,
    today: str,
    model: str,
    feedback: str = "",
    desk_rules: str = "",
    runner: AgentRunner | None = None,
    timeout_s: int = RUN_TIMEOUT_S,
) -> SkillRun:
    """One agent run in ``folder``: inputs first, then the agent, then its story.txt."""
    directory = skill_dir()
    missing = skill_missing(directory)
    if missing:
        return SkillRun(False, folder, error=f"the skill folder is missing {', '.join(missing)}")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "scripts").mkdir(exist_ok=True)
    for name in REQUIRED_FILES:
        shutil.copyfile(directory / name, folder / name)
    transcript = format_transcript(title, date, media, segments)
    (folder / "transcript.txt").write_text(transcript, encoding="utf-8", newline="\n")
    (folder / "documents.txt").write_text(documents, encoding="utf-8", newline="\n")
    (folder / "minutes-votes.txt").write_text(minutes_votes, encoding="utf-8", newline="\n")
    (folder / "roster.txt").write_text(roster, encoding="utf-8", newline="\n")
    first = meeting_stamp(segments[0].start) if segments else "0:00:00"
    last = meeting_stamp(segments[-1].start) if segments else "0:00:00"
    prompt = assignment_prompt(
        {
            "title": title,
            "date": date,
            "today": today,
            "line_count": f"{len(segments):,}",
            "first_stamp": first,
            "last_stamp": last,
        }
    )
    if desk_rules.strip():
        prompt += "\n## The desk's rules for this story\n" + desk_rules.strip() + "\n"
    if feedback.strip():
        prompt += (
            "\n## The editor's checking report on your last draft\n"
            "A previous draft failed the desk's deterministic check. Fix every problem below "
            "and write the whole deliverable again; every rule above still holds.\n"
            + feedback.strip()
            + "\n"
        )
    # A previous run's story is never read as this run's: each run gets its own folder.
    story_path = folder / "story.txt"
    story_path.unlink(missing_ok=True)
    run = runner or claude_agent
    started = time.monotonic()
    try:
        code, out, err = run(prompt, folder, model, timeout_s)
    except (OSError, subprocess.SubprocessError, ModelCallError) as exc:
        return SkillRun(False, folder, error=f"the writer could not start: {exc}")
    seconds = time.monotonic() - started
    if not story_path.is_file() or not story_path.read_text(encoding="utf-8").strip():
        reason = ""
        try:
            envelope = json.loads(out)
            reason = str(envelope.get("result") or envelope.get("subtype") or "")
        except ValueError:
            reason = (err or out).strip()[-300:]
        return SkillRun(
            False,
            folder,
            error=f"the writer finished without a story (exit {code}){': ' + reason if reason else ''}",
            seconds=seconds,
            version=skill_version(directory),
        )
    try:
        story = parse_skill_story(story_path.read_text(encoding="utf-8"))
    except SkillStoryError as exc:
        return SkillRun(
            False, folder, error=str(exc), seconds=seconds, version=skill_version(directory)
        )
    return SkillRun(True, folder, story=story, seconds=seconds, version=skill_version(directory))


def checks(text: str) -> list[str]:
    """CHECK-FIRST.txt as plain lines, at most three."""
    return [
        line.strip().lstrip("-*•·–— ").strip()
        for line in str(text or "").splitlines()
        if line.strip()
    ][:3]
