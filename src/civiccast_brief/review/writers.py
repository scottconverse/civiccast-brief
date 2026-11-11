"""The writers behind the desk's Redraft button.

A desk item keeps the published text, not the evidence it was written from. The weekly run's
work folder has that evidence: one folder per meeting with ``recap.json`` (and, when the run
had them, ``transcript.json`` and ``documents.json``). Redraft finds the item's folder there,
asks the city's model pick for a new draft with the editor's note and the current draft, and
runs the same checks as the weekly run: one rewrite with the check report, then anything still
unsupported is cut.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from civiccast_brief import models
from civiccast_brief.article.writer import (
    article_to_dict,
    caption_windows,
    check_article,
    drop_unsupported,
    load_documents,
    write_article,
)
from civiccast_brief.meeting import transcript as transcript_mod
from civiccast_brief.review.store import DeskItem, ReviewError
from civiccast_brief.script import checker
from civiccast_brief.script.recap import Recap, load_recap
from civiccast_brief.script.writer import ROUNDUP_ID, render_txt, write_script

#: (task, body) -> a function from prompt to answer text.
CompleteFor = Callable[[str, str], Callable[[str], str]]


def _item_key(recap: Recap) -> str:
    # The same key the episode and article stages give the desk item.
    if recap.video_id == ROUNDUP_ID:
        return f"roundup {recap.meeting_date}"
    return recap.video_id


def find_folder(work: Path, item: DeskItem) -> tuple[Path, Recap]:
    """The run folder whose recap the item was written from."""
    for path in sorted(Path(work).glob("*/recap.json")):
        try:
            recap = load_recap(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if _item_key(recap) == item.meeting_id:
            return path.parent, recap
    raise ReviewError(
        f"no meeting folder under {work} has the recap for {item.meeting_id}, so the writer "
        "has no claims to work from"
    )


def _picked(city: Path | None) -> CompleteFor:
    def complete_for(task: str, body: str) -> Callable[[str], str]:
        if city is None:
            raise ReviewError("start the desk with --city so Redraft knows which model to use")
        inner = models.complete_for(city, task, body)

        def complete(prompt: str) -> str:
            text, receipt = inner(prompt)
            print(f"redraft model: {receipt}", file=sys.stderr)
            return text

        return complete

    return complete_for


def desk_writer(
    work: Path, city: Path | None = None, *, complete_for: CompleteFor | None = None
) -> Callable[[DeskItem, str], Mapping[str, Any]]:
    """A Redraft writer for a desk fed by the weekly run in ``work``."""
    pick = complete_for or _picked(city)

    def writer(item: DeskItem, note: str) -> Mapping[str, Any]:
        folder, recap = find_folder(work, item)
        if item.kind == "article":
            return _story(folder, recap, item, note, pick)
        return _script(recap, item, note, pick)

    return writer


def _story(folder: Path, recap: Recap, item: DeskItem, note: str, pick: CompleteFor):
    complete = pick("article", recap.body)
    windows = None
    if (folder / "transcript.json").exists():
        data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        windows = caption_windows(recap, transcript_mod.from_json(data))
    pages = (
        load_documents(folder / "documents.json") if (folder / "documents.json").exists() else None
    )
    editor = (note, json.dumps(item.article or {}, indent=1, ensure_ascii=False))
    article = write_article(recap, complete, windows=windows, pages=pages, editor=editor)
    report = check_article(recap, article, windows, pages)
    if not report.passed:
        article = write_article(
            recap, complete, feedback=report.render(), windows=windows, pages=pages, editor=editor
        )
        report = check_article(recap, article, windows, pages)
    if not report.passed:
        article, _dropped = drop_unsupported(recap, article, windows, pages)
        report = check_article(recap, article, windows, pages)
    if not article.paragraphs:
        raise ReviewError("every paragraph of the new draft failed the check; nothing was changed")
    return {
        "article": article_to_dict(article),
        "checks_passed": report.passed,
        "checks_note": report.render(),
    }


def _script(recap: Recap, item: DeskItem, note: str, pick: CompleteFor):
    task = "roundup" if recap.video_id == ROUNDUP_ID else "script"
    complete = pick(task, recap.body)
    editor = (note, item.script)
    lines = write_script(recap, complete, editor=editor)
    report = checker.check_script(recap, lines)
    if not report.passed:
        lines = write_script(recap, complete, feedback=report.render(), editor=editor)
        report = checker.check_script(recap, lines)
    if not report.passed:
        lines, _dropped = checker.drop_unsupported(recap, lines)
        report = checker.check_script(recap, lines)
    return {
        "script": render_txt(lines),
        "checks_passed": report.passed,
        "checks_note": report.render(),
    }
