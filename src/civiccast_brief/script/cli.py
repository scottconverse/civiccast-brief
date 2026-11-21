"""Command line for the script writer and checker.

    python -m civiccast_brief.script write --recap recap.json --out script.txt [--feedback report.txt]
    python -m civiccast_brief.script check --recap recap.json --script script.json [--model-check]
    python -m civiccast_brief.script roundup --recap a.json --recap b.json --out roundup.json

``write`` saves the spoken script (``script.txt``, with ``[S1]``/``[S2]``/``[CHAPTER]`` lines) and
the same lines with their claim ids (``script.json``), then runs the deterministic check.
Exit 0 when the check passes, 1 when it fails or the model call fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from civiccast_brief import models
from civiccast_brief.meeting.roster import load_roster
from civiccast_brief.script import llm
from civiccast_brief.script.checker import (
    check_script,
    drop_empty_chapters,
    drop_unsupported,
    model_check,
)
from civiccast_brief.script.recap import Recap, channel_place, load_recap, save_recap
from civiccast_brief.script.roundup import RoundupError, combine
from civiccast_brief.script.writer import (
    ROUNDUP_ID,
    ScriptFormatError,
    load_script,
    save_script,
    write_script,
)


def _model_call(model: str) -> Callable[[str], str]:
    def complete(prompt: str) -> str:
        text, receipt = llm.chat(prompt, model=model)
        print(receipt.line(), file=sys.stderr)
        return text

    return complete


def _picked_call(city: Path, task: str, body: str) -> Callable[[str], str]:
    inner = models.complete_for(city, task, body)

    def complete(prompt: str) -> str:
        text, receipt = inner(prompt)
        print(receipt.line(), file=sys.stderr)
        return text

    return complete


def _writer(args: argparse.Namespace, recap) -> Callable[[str], str]:
    """``--model`` is an Ollama model; without it a ``--city`` write takes the city's pick
    (task "script", or "roundup" for a weekly roundup) for the recap's body."""
    if args.model or not args.city:
        return _model_call(args.model or llm.DEFAULT_MODEL)
    task = "roundup" if recap.video_id == ROUNDUP_ID else "script"
    return _picked_call(args.city, task, recap.body)


def _write(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    feedback = args.feedback.read_text(encoding="utf-8") if args.feedback else ""
    roster = load_roster(args.roster) if args.roster else None
    try:
        try:
            lines = write_script(recap, _writer(args, recap), feedback=feedback, roster=roster)
        except llm.ModelError as exc:
            # The model sometimes answers with nothing at all. One more ask is worth it: an
            # empty answer is not the story failing a check, it is no answer.
            if "empty" not in str(exc):
                raise
            print("the model answered nothing; asking once more", file=sys.stderr)
            lines = write_script(recap, _writer(args, recap), feedback=feedback, roster=roster)
    except (
        llm.ModelError,
        models.ModelCallError,
        models.ModelSettingsError,
        ScriptFormatError,
    ) as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return 1
    lines, marks = drop_empty_chapters(lines)
    for mark in marks:
        print(f"dropped empty chapter: {mark.text}")
    json_path = args.out.with_suffix(".json")
    save_script(lines, args.out, json_path)
    report = check_script(recap, lines, roster=roster)
    print(f"wrote {args.out} and {json_path}")
    print(report.render())
    return 0 if report.passed else 1


def _check(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    lines = load_script(args.script)
    roster = load_roster(args.roster) if args.roster else None
    report = check_script(recap, lines, roster=roster)
    if args.model_check:
        report = model_check(report, recap, _model_call(args.model))
    text = report.render()
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    return 0 if report.passed else 1


def _site(folder: Path) -> tuple[str, list[tuple[str, str]]]:
    """A city folder's name and the boards it carries from another city: a roster whose own
    city is not this one (a school district's, copied to every town on its channel)."""
    try:
        data = json.loads((folder / "city.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", []
    site = str(data.get("name") or "")
    shared: list[tuple[str, str]] = []
    for path in sorted((folder / "rosters").glob("*.json")):
        try:
            rostered = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name, body = str(rostered.get("city") or ""), str(rostered.get("body") or "")
        if site and name and body and name.casefold() != site.casefold():
            shared.append((name, body))
    return site, shared


def _recap_places(paths: list[Path], recaps: list[Recap]) -> dict[tuple[str, str], str]:
    """The place each recap's video channel names before its body, from the meeting's own
    record beside the recap. A carried board ("Board of Education") gets its own place
    ("Boulder Valley"), so the roundup never gives it the site's city."""
    places: dict[tuple[str, str], str] = {}
    for path, recap in zip(paths, recaps, strict=True):
        place = channel_place(recap, path.parent)
        if place:
            places[(recap.city.casefold(), recap.body.casefold())] = place
    return places


def _roundup(args: argparse.Namespace) -> int:
    site, shared = _site(args.city) if args.city else ("", [])
    recaps = [load_recap(path) for path in args.recap]
    try:
        recap = combine(
            recaps,
            city=site,
            shared=shared,
            places=_recap_places(args.recap, recaps),
        )
    except RoundupError as exc:
        print(f"roundup failed: {exc}", file=sys.stderr)
        return 1
    save_recap(recap, args.out)
    print(f"wrote {args.out}: {len(recap.claims)} claims from {len(args.recap)} meetings")
    return 0


def _repair(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    lines = load_script(args.script)
    roster = load_roster(args.roster) if args.roster else None
    kept, dropped = drop_unsupported(recap, lines, roster)
    json_path = args.out.with_suffix(".json")
    save_script(kept, args.out, json_path)
    for line in dropped:
        print(f"dropped {line.speaker}: {line.text}")
    report = check_script(recap, kept, roster=roster)
    print(f"wrote {args.out} and {json_path}")
    print(report.render())
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief.script")
    commands = parser.add_subparsers(dest="command", required=True)

    write = commands.add_parser("write", help="write a script from a recap, then check it")
    write.add_argument("--recap", type=Path, required=True)
    write.add_argument("--out", type=Path, required=True, help="the spoken script (.txt)")
    write.add_argument("--feedback", type=Path, help="checker problems to fix on a rerun")
    write.add_argument(
        "--model",
        help=f"an Ollama model (default: the --city pick in models.json, or {llm.DEFAULT_MODEL})",
    )
    write.add_argument("--city", type=Path, help="city folder (models.json)")
    write.add_argument(
        "--roster",
        type=Path,
        help="the body's roster file: a member's bare first name prints as the record's full name",
    )
    write.set_defaults(run=_write)

    check = commands.add_parser("check", help="check a script (script.json) against a recap")
    check.add_argument("--recap", type=Path, required=True)
    check.add_argument("--script", type=Path, required=True)
    check.add_argument("--model-check", action="store_true", help="also ask the model per line")
    check.add_argument("--model", default=llm.DEFAULT_MODEL)
    check.add_argument("--report", type=Path, help="also write the report to this file")
    check.add_argument(
        "--roster",
        type=Path,
        help="the body's roster file: a member's full name is allowed where a claim prints the first",
    )
    check.set_defaults(run=_check)

    repair = commands.add_parser("repair", help="drop lines that add facts, then re-check")
    repair.add_argument("--recap", type=Path, required=True)
    repair.add_argument("--script", type=Path, required=True, help="script.json to repair")
    repair.add_argument("--out", type=Path, required=True, help="the repaired script (.txt)")
    repair.add_argument(
        "--roster",
        type=Path,
        help="the body's roster file: a member's full name is allowed where a claim prints the first",
    )
    repair.set_defaults(run=_repair)

    roundup = commands.add_parser("roundup", help="combine meeting recaps into one roundup recap")
    roundup.add_argument("--recap", type=Path, action="append", required=True)
    roundup.add_argument("--out", type=Path, required=True, help="the roundup recap.json")
    roundup.add_argument(
        "--city", type=Path, help="the city folder: a board it shares from another city counts"
    )
    roundup.set_defaults(run=_roundup)

    args = parser.parse_args(argv)
    return args.run(args)
