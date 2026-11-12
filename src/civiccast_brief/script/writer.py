"""Recap to two-host script.

The writer sees only the recap's claims. The model returns one JSON object per line, each with
the claim ids the line relies on. ``render_txt`` strips those ids for the spoken script; the
JSON keeps them so the checker can map each line to its claims.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from civiccast_brief.script.recap import Recap

DISCLAIMER = (
    "This episode is AI-generated from the public meeting video; check the official record."
)
SPEAKERS = {"S1": "Maya", "S2": "Theo"}
CHAPTER = "CHAPTER"
_VALID_SPEAKERS = frozenset({"S1", "S2", CHAPTER})


@dataclass(frozen=True)
class ScriptLine:
    speaker: str
    text: str
    claim_ids: tuple[str, ...] = ()

    @property
    def is_chapter(self) -> bool:
        return self.speaker == CHAPTER


class ScriptFormatError(ValueError):
    """The model answer is not a script in the agreed line format."""


PROMPT_HEAD = """You write the script for a two-host audio episode about one city council meeting. A text-to-speech voice reads the script aloud, so the words must sound right when spoken.

Hosts: Maya (speaker S1) opens and leads. Theo (speaker S2) adds context and closes. They trade the points. Keep every line under 45 words.

FACTS: use only the claims listed at the end. Every fact, number, name, vote, date and outcome in the script must come from a claim. Add no facts, causes, reasons, background or outside context. Do not quote the claims.

OUTPUT: one JSON object per line and nothing else. No prose, no code fence, no blank lines. Three kinds of line:
{"speaker": "S1", "text": "...", "claim_ids": ["c03"]}
{"speaker": "S2", "text": "...", "claim_ids": ["c04", "c05"]}
{"speaker": "CHAPTER", "text": "Chapter title", "claim_ids": ["c06"]}
claim_ids lists every claim the line relies on. Use an empty list only for a short reaction ("Right.") or a question that states no fact.

STRUCTURE, in this order:
1. Maya (S1): an intro that names CivicCast Brief, the city, the body and the meeting date, from the meeting header below. It needs no claim id; cite one only if the intro uses a fact from it.
2. Theo (S2): this sentence, exactly: This episode is AI-generated from the public meeting video; check the official record.
3. The meeting's items in the order of the claims. A CHAPTER line starts each agenda item (an ordinance, resolution, hearing, appeal, public comment, or council reports) and is titled with that item's plain name in 45 characters or fewer, such as "Art in Public Places mural" or "Setback appeal". Do not start a chapter for a single remark; put the remark under the item it belongs to. For each item: what it is, the main points made, then the vote count and outcome. Cover public comment.
4. Last line: Theo (S2) closes and points listeners to the meeting video on the city's {video_place}. This line states no other fact and needs no claim id.

WRITE FOR THE EAR. Spell numbers, codes and addresses the way a radio host would say them, in words. Examples of the form (use one only when a claim has that fact): "Ordinance twenty twenty-six dash sixty-nine", "two hundred thousand dollars", "eighty-five thirteen Main Street", "March first, twenty twenty-seven", "six to one", "about seven hundred spaces".
No digits anywhere in the text. Say an initialism letter by letter with hyphens, such as R-T-D or H-O-A. Short sentences. Natural conversation: an occasional light reaction ("Right.", "That's a big number.") and a question one host asks the other. Alternate the speakers. No jokes about people.

SOUND LIKE TWO PEOPLE TALKING, NOT A LIST:
- Never repeat a sentence, and never say the same fact twice. Skip a claim that adds nothing new (a second "the vote carried" for the same item).
- Combine related claims about one item into one or two flowing lines, citing all of their ids.
- Do not start more than two lines in a row the same way. When a claim says "a speaker" or "an unidentified speaker", say "one speaker" or "another speaker", and never invent who they are (no roles, names or titles the claim lacks).
- Tie each vote to its item: "On the mural, the vote was unanimous", not a bare "A vote carried."

LENGTH: 850 to 1000 spoken words in total.
"""


ROUNDUP_ID = "roundup"
ROUNDUP_NOTE = """THIS IS A WEEKLY ROUNDUP of several meetings, not one meeting. Each claim's text starts with the date of its meeting in brackets. The intro names the meeting dates from the header. Start a CHAPTER for each meeting, titled with its date, such as "October twentieth meeting", then cover that meeting's main items in the order of its claims. Pick the items with votes and outcomes first; you do not need every claim. The last line points listeners to the meeting videos on the city's {video_place}."""


#: A redraft from the desk: the editor's note and the script it is about.
EDITOR_NOTE = (
    "The editor read the script below and asks for this change: {note}\n"
    "Make that change. Every rule above still holds: use only the claims given here, and "
    "output the whole script again in the same format.\n"
    "The script:\n{draft}"
)


def build_prompt(recap: Recap, feedback: str = "", editor: tuple[str, str] | None = None) -> str:
    claims = [{"id": c.id, "topic": c.topic, "text": c.text} for c in recap.claims]
    parts = [
        PROMPT_HEAD.replace("{video_place}", recap.video_place),
        f"Meeting (from the recap header): {recap.body} of {recap.city}, {recap.meeting_date}.",
    ]
    if recap.video_id == ROUNDUP_ID:
        parts.insert(1, ROUNDUP_NOTE.replace("{video_place}", recap.video_place))
    if editor is not None:
        parts.append(EDITOR_NOTE.format(note=editor[0].strip(), draft=editor[1].strip()))
    if feedback.strip():
        parts.append(
            "A previous draft failed the no-new-claims check. Fix every problem below and output "
            "the whole script again in the same format:\n" + feedback.strip()
        )
    parts.append(
        "Claims (the only facts you may use):\n" + json.dumps(claims, indent=1, ensure_ascii=False)
    )
    return "\n\n".join(parts) + "\n"


def parse_script(answer: str) -> list[ScriptLine]:
    """Read the JSON-lines answer. Fences and blank lines are ignored; anything else must be
    one JSON object per line."""
    lines: list[ScriptLine] = []
    bad: list[str] = []
    for raw in answer.splitlines():
        text = raw.strip()
        if not text or text.startswith("```"):
            continue
        text = text.rstrip(",")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            bad.append(text[:120])
            continue
        if not isinstance(obj, dict):
            bad.append(text[:120])
            continue
        lines.append(_line_from_dict(obj, bad, text))
    if bad:
        raise ScriptFormatError(f"{len(bad)} line(s) are not script JSON, e.g. {bad[0]!r}")
    if not lines:
        raise ScriptFormatError("the model answer holds no script lines")
    return lines


def _line_from_dict(obj: dict[str, Any], bad: list[str], raw: str) -> ScriptLine:
    speaker = str(obj.get("speaker", ""))
    text = str(obj.get("text", "")).strip()
    claim_ids = obj.get("claim_ids", [])
    if speaker not in _VALID_SPEAKERS or not text or not isinstance(claim_ids, list):
        bad.append(raw[:120])
        return ScriptLine(speaker=speaker, text=text, claim_ids=())
    return ScriptLine(speaker=speaker, text=text, claim_ids=tuple(str(c) for c in claim_ids))


def write_script(
    recap: Recap,
    complete: Callable[[str], str],
    *,
    feedback: str = "",
    editor: tuple[str, str] | None = None,
) -> list[ScriptLine]:
    """Ask the model for the script and parse it. ``complete`` takes the prompt and returns
    the model's answer text. ``editor`` is (the editor's note, the script it is about)."""
    return parse_script(complete(build_prompt(recap, feedback, editor)))


def render_txt(lines: list[ScriptLine]) -> str:
    """The spoken script: ``[S1] text``, ``[S2] text`` and ``[CHAPTER Title]`` lines."""
    out = []
    for line in lines:
        if line.is_chapter:
            out.append(f"[CHAPTER {line.text}]")
        else:
            out.append(f"[{line.speaker}] {line.text}")
    return "\n".join(out) + "\n"


_TXT_CHAPTER = re.compile(r"\[CHAPTER\s+(.+?)\]")
_TXT_LINE = re.compile(r"\[(S1|S2)\]\s*(.+)")


def parse_txt(text: str) -> list[ScriptLine]:
    """The lines of a spoken script (:func:`render_txt`'s format), as the desk keeps it.

    The desk keeps only the words, so the lines carry no claim ids."""
    lines: list[ScriptLine] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if chapter := _TXT_CHAPTER.fullmatch(line):
            lines.append(ScriptLine(CHAPTER, chapter[1].strip()))
        elif spoken := _TXT_LINE.fullmatch(line):
            lines.append(ScriptLine(spoken[1], spoken[2].strip()))
        else:
            raise ScriptFormatError(f"not a script line: {line[:120]!r}")
    if not lines:
        raise ScriptFormatError("the script has no lines")
    return lines


def script_to_dict(lines: list[ScriptLine]) -> list[dict[str, Any]]:
    return [
        {"speaker": line.speaker, "text": line.text, "claim_ids": list(line.claim_ids)}
        for line in lines
    ]


def script_from_dict(data: list[dict[str, Any]]) -> list[ScriptLine]:
    if not isinstance(data, list):
        raise ScriptFormatError("script JSON must be a list of lines")
    bad: list[str] = []
    lines = [_line_from_dict(item, bad, json.dumps(item)) for item in data]
    if bad:
        raise ScriptFormatError(f"script JSON has a bad line: {bad[0]}")
    return lines


def save_script(lines: list[ScriptLine], txt_path: Path, json_path: Path) -> None:
    Path(txt_path).write_text(render_txt(lines), encoding="utf-8")
    Path(json_path).write_text(
        json.dumps(script_to_dict(lines), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_script(json_path: Path) -> list[ScriptLine]:
    return script_from_dict(json.loads(Path(json_path).read_text(encoding="utf-8")))
