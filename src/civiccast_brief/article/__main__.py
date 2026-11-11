"""Command line for the article writer.

    python -m civiccast_brief.article write --recap recap.json --city cities/longmont --out run/article
    python -m civiccast_brief.article check --recap recap.json --article run/article.json
    python -m civiccast_brief.article title --recap roundup/recap.json --city cities/longmont
    python -m civiccast_brief.article stage --db desk.db --recap recap.json --article run/article.json

``write`` picks the model from the city's ``models.json`` (task "article", the recap's body), or
from ``--model provider:model``. It writes, checks, and when the check fails it writes once more
with the report as feedback. A paragraph that still fails is dropped. It saves ``article.json``
(paragraphs with their video citations) and ``article.md``. Exit 0 when the saved article passes.

``write``, ``check`` and ``stage`` read the meeting's documents from ``--documents`` (default:
``documents.json`` next to the recap, when it is there). A paragraph may then cite document
pages as well as claims, and the check looks in the cited pages too.

``title`` writes a checked episode title (model task "roundup") to ``title.json`` next to the
recap. Exit 1 and no file when no draft passes; the episode then keeps its dated title.

``stage`` checks a saved story again and puts it on the editor desk as an article item. The
check result goes with it, so a failing story is never auto-published. A producer approves it
on the desk; ``episode assemble --article`` then adds the published story to the episode.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from civiccast_brief import models
from civiccast_brief.article.title import save_title, write_title
from civiccast_brief.article.writer import (
    ArticleFormatError,
    DocPage,
    article_to_dict,
    caption_windows,
    check_article,
    drop_unsupported,
    load_article,
    load_documents,
    save_article,
    write_article,
)
from civiccast_brief.meeting import transcript as transcript_mod
from civiccast_brief.review.desk import stage_item
from civiccast_brief.review.store import ReviewError
from civiccast_brief.script.recap import load_recap


def _complete(args: argparse.Namespace, body: str, task: str = "article") -> Callable[[str], str]:
    if args.model:
        provider, model = models.split_pick(args.model)
        inner = (
            models.claude_complete(model, task=task, body=body)
            if provider == "claude"
            else models.ollama_complete(model, task=task, body=body)
        )
    else:
        inner = models.complete_for(args.city, task, body)

    def complete(prompt: str) -> str:
        text, receipt = inner(prompt)
        print(f"model: {receipt}", file=sys.stderr)
        return text

    return complete


def _windows(args: argparse.Namespace, recap) -> dict[str, str] | None:
    path = args.transcript or args.recap.with_name("transcript.json")
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return caption_windows(recap, transcript_mod.from_json(data))


def _pages(args: argparse.Namespace) -> dict[str, DocPage] | None:
    path = args.documents or args.recap.with_name("documents.json")
    if not path.exists():
        return None
    return load_documents(path)


def _write(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    complete = _complete(args, recap.body)
    windows = _windows(args, recap)
    pages = _pages(args)
    try:
        article = write_article(recap, complete, windows=windows, pages=pages)
        report = check_article(recap, article, windows, pages)
        if not report.passed:
            print("first draft failed the check; writing again with the report", file=sys.stderr)
            print(report.render(), file=sys.stderr)
            article = write_article(
                recap, complete, feedback=report.render(), windows=windows, pages=pages
            )
            report = check_article(recap, article, windows, pages)
    except (models.ModelCallError, ArticleFormatError) as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return 1
    if not report.passed:
        article, dropped = drop_unsupported(recap, article, windows, pages)
        for line in dropped.lines():
            print(line, file=sys.stderr)
        report = check_article(recap, article, windows, pages)
    json_path, md_path = save_article(recap, article, args.out, pages)
    print(f"wrote {json_path} and {md_path}")
    print(report.render())
    return 0 if report.passed and article.paragraphs else 1


def _title(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    complete = _complete(args, recap.body, task="roundup")
    try:
        title = write_title(recap, complete, log=lambda m: print(m, file=sys.stderr))
    except models.ModelCallError as exc:
        print(f"title failed: {exc}", file=sys.stderr)
        return 1
    if title is None:
        print("no title passed the check; the episode keeps its dated title", file=sys.stderr)
        return 1
    out = save_title(title, args.out or args.recap.with_name("title.json"))
    print(f"wrote {out}: {title.text}")
    return 0


def _check(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    report = check_article(recap, load_article(args.article), _windows(args, recap), _pages(args))
    print(report.render())
    return 0 if report.passed else 1


def _stage(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    try:
        article = load_article(args.article)
    except (OSError, ValueError) as exc:
        print(f"stage failed: {exc}", file=sys.stderr)
        return 1
    report = check_article(recap, article, _windows(args, recap), _pages(args))
    try:
        item = stage_item(
            args.db,
            args.city,
            kind="article",
            meeting_id=recap.video_id,
            title=article.headline,
            meeting_date=recap.meeting_date,
            body=recap.body,
            checks_passed=report.passed,
            checks_note=report.render(),
            article=article_to_dict(article),
        )
    except ReviewError as exc:
        print(f"stage failed: {exc}", file=sys.stderr)
        return 1
    print(report.render())
    print(f"staged article item {item.id} ({item.status})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief.article")
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write", help="write and check the article for one meeting")
    write.add_argument("--recap", type=Path, required=True)
    write.add_argument("--out", type=Path, required=True, help="path without suffix")
    write.add_argument("--city", type=Path, default=Path("."), help="city folder (models.json)")
    write.add_argument("--model", help="provider:model, overrides models.json")
    write.add_argument("--transcript", type=Path, help="default: transcript.json next to the recap")
    write.add_argument("--documents", type=Path, help="default: documents.json next to the recap")
    write.set_defaults(func=_write)
    title = sub.add_parser("title", help="write and check an episode title (title.json)")
    title.add_argument("--recap", type=Path, required=True)
    title.add_argument("--out", type=Path, help="default: title.json next to the recap")
    title.add_argument("--city", type=Path, default=Path("."), help="city folder (models.json)")
    title.add_argument("--model", help="provider:model, overrides models.json")
    title.set_defaults(func=_title)
    check = sub.add_parser("check", help="check a saved article against its recap")
    check.add_argument("--recap", type=Path, required=True)
    check.add_argument("--article", type=Path, required=True)
    check.add_argument("--transcript", type=Path, help="default: transcript.json next to the recap")
    check.add_argument("--documents", type=Path, help="default: documents.json next to the recap")
    check.set_defaults(func=_check)
    stage = sub.add_parser("stage", help="check a saved article and put it on the editor desk")
    stage.add_argument("--db", type=Path, required=True, help="desk database")
    stage.add_argument("--recap", type=Path, required=True)
    stage.add_argument("--article", type=Path, required=True)
    stage.add_argument("--city", type=Path, help="city folder (desk.json auto-publish)")
    stage.add_argument("--transcript", type=Path, help="default: transcript.json next to the recap")
    stage.add_argument("--documents", type=Path, help="default: documents.json next to the recap")
    stage.set_defaults(func=_stage)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
