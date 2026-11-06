"""The cited recap: the prompt for one time window of a meeting, and the model's draft items.

A meeting is cut into windows (30 minutes by default) so each answer fits the model's output cap.
Each window gets the official agenda, the roster, the speaker key for the window, and the caption
lines. The model returns draft items; verify.py decides which of them stand.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from civiccast_brief.meeting.roster import Roster
from civiccast_brief.meeting.speakers import Turn
from civiccast_brief.meeting.transcript import Segment, hms

DEFAULT_WINDOW_S = 1800.0
MAX_ITEMS_PER_WINDOW = 12


@dataclass(frozen=True)
class Window:
    index: int
    start: float
    end: float
    first: int
    last: int  # inclusive segment indices


def windows(segments: list[Segment], window_s: float = DEFAULT_WINDOW_S) -> list[Window]:
    """Cut the caption lines into consecutive windows by start time. A window holds whole lines."""
    cut: dict[int, list[int]] = {}
    for position, segment in enumerate(segments):
        cut.setdefault(int(segment.start // window_s), []).append(position)
    result: list[Window] = []
    for number, key in enumerate(sorted(cut), start=1):
        positions = cut[key]
        result.append(
            Window(
                index=number,
                start=segments[positions[0]].start,
                end=segments[positions[-1]].end,
                first=positions[0],
                last=positions[-1],
            )
        )
    return result


def transcript_lines(segments: list[Segment], window: Window) -> str:
    lines = []
    for segment in segments[window.first : window.last + 1]:
        mark = ">> " if segment.turn_start else ""
        lines.append(f"{hms(segment.start)}  {mark}{segment.text}")
    return "\n".join(lines)


def speaker_key(turns: list[Turn], window: Window) -> str:
    rows = []
    for turn in turns:
        if not (window.first <= turn.first <= window.last):
            continue
        if turn.speaker:
            rows.append(f"- {hms(turn.start)}: {turn.speaker} (roll call)")
        elif turn.title:
            rows.append(f"- {hms(turn.start)}: a {turn.title}, name not matched (roll call)")
    return "\n".join(rows) or "- none in this part"


PROMPT = """You are writing the fact list for one part of a city council meeting. The source is the auto-caption transcript at the end of this message. Every fact needs a quote from that transcript that proves it.

MEETING: {title}, {meeting_date}. Part {part} of {parts}, covering {start} to {end}.

OFFICIAL AGENDA (use these spellings for boards, streets and items; the captions often mishear them):
{agenda}

COUNCIL ROSTER (official names): {roster}

SPEAKER KEY (the only speakers named in this part; anyone else is unknown):
{speaker_key}

RULES
1. Output only a JSON array. Each item is an object with the keys "topic", "text", "timestamp" and "quote".
2. "quote": copy words exactly from the transcript lines, with the same spelling, capitals and punctuation. Join consecutive lines with one space. Leave out ">>" and the timestamps. Keep it to one to three lines.
3. "timestamp": the time (H:MM:SS) of the transcript line where the quote starts.
4. "text": one plain past-tense sentence stating one fact the quote supports. Add nothing the quote does not say.
5. Cover decisions (approved, denied, continued, directed), votes with the count as stated, dollar amounts and budgets, dates and deadlines, and public comment (what a speaker asked for or opposed). Skip greetings, the pledge, the roll call and procedure that decides nothing.
6. Names and roles: name a person only when the speaker key names that person for that time, or the quote itself contains the name. Use a role word ("city manager", "council member", "resident", "applicant", "presiding officer") only when the quote itself says that role, or the speaker key gives "council member" for that time. Otherwise write "an unidentified speaker". Never guess a name or a role from the transcript.
7. Spell boards, streets and items as the OFFICIAL AGENDA does. Do not copy a misheard term into "text".
8. At most {max_items} items. If nothing in this part qualifies, output [].

TRANSCRIPT ("H:MM:SS  text"; ">>" marks a change of speaker):
{lines}
"""


def build_prompt(
    *,
    title: str,
    meeting_date: str,
    part: int,
    parts: int,
    window: Window,
    segments: list[Segment],
    turns: list[Turn],
    roster: Roster,
    agenda_text: str,
) -> str:
    roster_line = "; ".join(f"{m.name} ({m.role})" for m in roster.members)
    return PROMPT.format(
        title=title,
        meeting_date=meeting_date,
        part=part,
        parts=parts,
        start=hms(window.start),
        end=hms(window.end),
        agenda=agenda_text or "(no agenda file was given: use the roster spellings only)",
        roster=roster_line,
        speaker_key=speaker_key(turns, window),
        max_items=MAX_ITEMS_PER_WINDOW,
        lines=transcript_lines(segments, window),
    )


class DraftFormatError(ValueError):
    """The model answer does not contain a JSON array of items."""


def parse_items(answer: str) -> list[dict[str, Any]]:
    """Read the JSON array in the answer, ignoring any text around it."""
    start, end = answer.find("["), answer.rfind("]")
    if start == -1 or end <= start:
        raise DraftFormatError("no JSON array in the model answer")
    try:
        data = json.loads(answer[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DraftFormatError(f"model answer is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise DraftFormatError("model answer is not a JSON array")
    items = [item for item in data if isinstance(item, dict)]
    return items


@dataclass
class WindowDraft:
    window: int
    items: list[dict[str, Any]]
    answer: str
    receipt: str
    error: str | None


def draft_window(
    complete: Callable[[str], tuple[str, Any]], prompt: str, window: int
) -> WindowDraft:
    """Call the model for one window. A format error is recorded on the draft, not raised."""
    answer, receipt = complete(prompt)
    try:
        items = parse_items(answer)
        return WindowDraft(window, items, answer, _receipt_line(receipt), None)
    except DraftFormatError as exc:
        return WindowDraft(window, [], answer, _receipt_line(receipt), str(exc))


def _receipt_line(receipt: Any) -> str:
    line = getattr(receipt, "line", None)
    return line() if callable(line) else str(receipt)
