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
import sys
from collections.abc import Callable
from pathlib import Path

from civiccast_brief import models
from civiccast_brief.script import llm
from civiccast_brief.script.checker import check_script, drop_unsupported, model_check
from civiccast_brief.script.recap import load_recap, save_recap
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
    try:
        lines = write_script(recap, _writer(args, recap), feedback=feedback)
    except (
        llm.ModelError,
        models.ModelCallError,
        models.ModelSettingsError,
        ScriptFormatError,
    ) as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return 1
    json_path = args.out.with_suffix(".json")
    save_script(lines, args.out, json_path)
    report = check_script(recap, lines)
    print(f"wrote {args.out} and {json_path}")
    print(report.render())
    return 0 if report.passed else 1


def _check(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    lines = load_script(args.script)
    report = check_script(recap, lines)
    if args.model_check:
        report = model_check(report, recap, _model_call(args.model))
    text = report.render()
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    return 0 if report.passed else 1


def _roundup(args: argparse.Namespace) -> int:
    try:
        recap = combine([load_recap(path) for path in args.recap])
    except RoundupError as exc:
        print(f"roundup failed: {exc}", file=sys.stderr)
        return 1
    save_recap(recap, args.out)
    print(f"wrote {args.out}: {len(recap.claims)} claims from {len(args.recap)} meetings")
    return 0


def _repair(args: argparse.Namespace) -> int:
    recap = load_recap(args.recap)
    lines = load_script(args.script)
    kept, dropped = drop_unsupported(recap, lines)
    json_path = args.out.with_suffix(".json")
    save_script(kept, args.out, json_path)
    for line in dropped:
        print(f"dropped {line.speaker}: {line.text}")
    report = check_script(recap, kept)
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
    write.set_defaults(run=_write)

    check = commands.add_parser("check", help="check a script (script.json) against a recap")
    check.add_argument("--recap", type=Path, required=True)
    check.add_argument("--script", type=Path, required=True)
    check.add_argument("--model-check", action="store_true", help="also ask the model per line")
    check.add_argument("--model", default=llm.DEFAULT_MODEL)
    check.add_argument("--report", type=Path, help="also write the report to this file")
    check.set_defaults(run=_check)

    repair = commands.add_parser("repair", help="drop lines that add facts, then re-check")
    repair.add_argument("--recap", type=Path, required=True)
    repair.add_argument("--script", type=Path, required=True, help="script.json to repair")
    repair.add_argument("--out", type=Path, required=True, help="the repaired script (.txt)")
    repair.set_defaults(run=_repair)

    roundup = commands.add_parser("roundup", help="combine meeting recaps into one roundup recap")
    roundup.add_argument("--recap", type=Path, action="append", required=True)
    roundup.add_argument("--out", type=Path, required=True, help="the roundup recap.json")
    roundup.set_defaults(run=_roundup)

    args = parser.parse_args(argv)
    return args.run(args)
