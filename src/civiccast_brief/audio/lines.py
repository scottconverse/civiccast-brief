"""Parse a two-host script into spoken lines and chapter marks.

One item per line:

    [S1] spoken text        S1 is Maya (female host).
    [S2] spoken text        S2 is Theo (male host).
    [CHAPTER Title]         A chapter mark. Never spoken. It starts the next spoken line.

Blank lines are ignored. Anything else is an error, so a stray stage direction or heading cannot
reach a voice engine unnoticed. Voice engines only ever receive the spoken lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from civiccast_brief.audio.errors import ScriptError

SPEAKERS = ("S1", "S2")

_SPOKEN = re.compile(r"\[(S[12])\]\s*(\S.*)")
_CHAPTER = re.compile(r"\[CHAPTER\s+(\S.*?)\s*\]")


@dataclass(frozen=True)
class Line:
    """One spoken line. `number` is the 1-based line in the source file, for error messages."""

    number: int
    speaker: str
    text: str


@dataclass(frozen=True)
class Chapter:
    """A chapter mark. `line_index` is the index in Script.lines of the line it precedes."""

    title: str
    line_index: int


@dataclass(frozen=True)
class Script:
    lines: tuple[Line, ...]
    chapters: tuple[Chapter, ...]


def parse_script(text: str) -> Script:
    """Parse script text. Raises ScriptError with the source line number on any problem."""
    lines: list[Line] = []
    chapters: list[Chapter] = []
    pending: tuple[str, int] | None = None  # (title, source line) of a mark with no line yet

    for number, raw in enumerate(text.splitlines(), start=1):
        item = raw.strip()
        if not item:
            continue
        mark = _CHAPTER.fullmatch(item)
        if mark is not None:
            if pending is not None:
                raise ScriptError(
                    f"line {pending[1]}: chapter {pending[0]!r} has no spoken line before "
                    f"the next chapter (line {number})"
                )
            pending = (mark.group(1), number)
            continue
        spoken = _SPOKEN.fullmatch(item)
        if spoken is None:
            raise ScriptError(
                f"line {number}: expected [S1] text, [S2] text or [CHAPTER Title]; "
                f"got {item[:60]!r}"
            )
        if pending is not None:
            chapters.append(Chapter(pending[0], len(lines)))
            pending = None
        lines.append(Line(number, spoken.group(1), spoken.group(2).strip()))

    if pending is not None:
        raise ScriptError(f"line {pending[1]}: chapter {pending[0]!r} has no spoken line after it")
    if not lines:
        raise ScriptError("script has no spoken lines")
    return Script(tuple(lines), tuple(chapters))


def read_script(path: Path) -> Script:
    """Read a UTF-8 script file (a byte-order mark is tolerated) and parse it."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScriptError(f"script {path} is not UTF-8 text") from exc
    except OSError as exc:
        raise ScriptError(f"cannot read script {path}: {exc}") from exc
    return parse_script(text)
