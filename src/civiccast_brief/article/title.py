"""A written episode title, checked like an article headline.

The weekly roundup used to be titled only by its dates ("Longmont weekly roundup: October 6,
2026 and October 20, 2026"). Here a model writes a news title from the roundup's claims and
lists the claims it uses. The title passes only when every number and name in it is in those
claims or in the header (city, bodies, dates), the same check an article headline gets. When it
still fails after one rewrite, there is no written title and the dated title stays.

The title is saved as ``title.json`` next to ``recap.json``. ``episode.build.assemble`` checks it
again before it uses it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from civiccast_brief.article.writer import Issue, _check_text
from civiccast_brief.script.recap import Recap

MAX_WORDS = 14

PROMPT = """You write the title of a weekly news podcast episode that covers several public meetings of one city.

FACTS: use only the claims listed at the end. Every number, name and outcome in the title must come from a claim you list. Add nothing else.

OUTPUT: one JSON object and nothing else, no code fence:
{"title": "...", "claim_ids": ["m1-c03", "m2-c01"]}
- claim_ids lists every claim the title relies on, at least one.
- The title names the week's one or two most important decisions, in 12 words or fewer, in plain news style (present tense, no question, no colon, no quotes). Spell boards, streets and items as the claims do. Do not put the dates in the title.
"""


class TitleFormatError(ValueError):
    """The model answer is not a title in the agreed format."""


@dataclass(frozen=True)
class Title:
    text: str
    claim_ids: tuple[str, ...]


def build_prompt(recap: Recap, feedback: str = "") -> str:
    claims = [{"id": c.id, "text": c.text} for c in recap.claims]
    parts = [
        PROMPT,
        f"Meetings (from the header): {recap.body} of {recap.city}, {recap.meeting_date}.",
    ]
    if feedback.strip():
        parts.append(
            "A previous title failed the check. Fix every problem below and output the JSON "
            "again:\n" + feedback.strip()
        )
    parts.append(
        "Claims (the only facts you may use):\n" + json.dumps(claims, indent=1, ensure_ascii=False)
    )
    return "\n\n".join(parts) + "\n"


def parse_title(answer: str) -> Title:
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        raise TitleFormatError("no JSON object in the model answer")
    try:
        data = json.loads(answer[start : end + 1])
    except json.JSONDecodeError as exc:
        raise TitleFormatError(f"model answer is not valid JSON: {exc}") from exc
    return title_from_dict(data)


def title_from_dict(data: Any) -> Title:
    if not isinstance(data, dict):
        raise TitleFormatError("title JSON must be an object")
    text = " ".join(str(data.get("title", "")).split())
    ids = data.get("claim_ids")
    if not text or not isinstance(ids, list):
        raise TitleFormatError("title JSON needs a title and a list of claim_ids")
    return Title(text=text, claim_ids=tuple(str(i) for i in ids))


def check_title(recap: Recap, title: Title) -> list[Issue]:
    issues: list[Issue] = []
    words = len(title.text.split())
    if words > MAX_WORDS:
        issues.append(Issue("title", "length", f"{words} words; the limit is {MAX_WORDS}"))
    _check_text("title", title.text, title.claim_ids, recap, issues)
    return issues


def render_issues(issues: list[Issue]) -> str:
    return "\n".join(f"{i.where} {i.code}: {i.message}" for i in issues)


def write_title(
    recap: Recap, complete: Callable[[str], str], log: Callable[[str], None] = lambda _: None
) -> Title | None:
    """Write, check, and rewrite once with the problems. None when no draft passes."""
    feedback = ""
    for _attempt in range(2):
        try:
            title = parse_title(complete(build_prompt(recap, feedback)))
        except TitleFormatError as exc:
            log(f"bad title answer: {exc}")
            feedback = f"title format: {exc}"
            continue
        issues = check_title(recap, title)
        if not issues:
            return title
        feedback = render_issues(issues)
        log(f"title failed the check: {title.text!r}\n{feedback}")
    return None


def save_title(title: Title, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"title": title.text, "claim_ids": list(title.claim_ids)}, indent=2, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def load_title(path: Path) -> Title:
    return title_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
