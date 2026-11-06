"""Recap to two-host script.

The writer sees only the recap's claims. The model returns one JSON object per line, each with
the claim ids the line relies on. ``render_txt`` strips those ids for the spoken script; the
JSON keeps them so the checker can map each line to its claims.
"""

from __future__ import annotations

import json
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
1. Maya (S1): an intro that names CivicCast Brief, the city of Longmont, the body and the meeting date. Cite the claim that gives the date.
2. Theo (S2): this sentence, exactly: This episode is AI-generated from the public meeting video; check the official record.
3. The meeting's items in the order of the claims. Put a CHAPTER line where the topic changes, then the lines for that topic. Give each item its vote count and outcome, and the main arguments on each side. Cover public comment.
4. Last line: Theo (S2) closes and points listeners to the meeting video on the city's YouTube channel. Cite the claim that says so.

WRITE FOR THE EAR. Spell numbers, codes and addresses the way a radio host would say them, in words. Examples of the form (use one only when a claim has that fact): "Ordinance twenty twenty-six dash sixty-nine", "two hundred thousand dollars", "eighty-five thirteen Saint Vrain Road", "March first, twenty twenty-seven", "six to one", "about seven hundred spaces".
No digits anywhere in the text. Spell out RTD as R-T-D. Short sentences. Natural conversation: an occasional light reaction ("Right.", "That's a big number.") and a question one host asks the other. Alternate the speakers. No jokes about people.

LENGTH: 850 to 1000 spoken words in total.
"""


def build_prompt(recap: Recap, feedback: str = "") -> str:
    claims = [{"id": c.id, "topic": c.topic, "text": c.text} for c in recap.claims]
    parts = [
        PROMPT_HEAD,
        f"Meeting (from the recap header): {recap.body} of {recap.city}, {recap.meeting_date}.",
    ]
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
    recap: Recap, complete: Callable[[str], str], *, feedback: str = ""
) -> list[ScriptLine]:
    """Ask the model for the script and parse it. ``complete`` takes the prompt and returns
    the model's answer text."""
    return parse_script(complete(build_prompt(recap, feedback)))


def render_txt(lines: list[ScriptLine]) -> str:
    """The spoken script: ``[S1] text``, ``[S2] text`` and ``[CHAPTER Title]`` lines."""
    out = []
    for line in lines:
        if line.is_chapter:
            out.append(f"[CHAPTER {line.text}]")
        else:
            out.append(f"[{line.speaker}] {line.text}")
    return "\n".join(out) + "\n"


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
