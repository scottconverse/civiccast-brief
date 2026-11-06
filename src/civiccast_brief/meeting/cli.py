"""Command line for the Phase 4 meeting pipeline.

    python -m civiccast_brief.meeting run <youtube_video_id> --out DIR
    python -m civiccast_brief.meeting run <clip_id> --granicus HOST --view-id N --out DIR
        [--agenda AGENDA.txt] [--roster ROSTER.json] [--townrecord-python PATH]
        [--window-minutes 30] [--model NAME] [--reuse-draft]

Writes transcript.json, speakers.json and recap.json (plus draft.json, verify-report.json and
summary.json) under DIR. Exit 0 when every stage finished; 1 when capture or the pipeline failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from civiccast_brief.meeting import capture as capture_mod
from civiccast_brief.meeting import granicus as granicus_mod
from civiccast_brief.meeting.pipeline import PipelineError, run
from civiccast_brief.meeting.recap import DEFAULT_WINDOW_S
from civiccast_brief.script import llm


def _run(args: argparse.Namespace) -> int:
    try:
        summary = run(
            args.video_id,
            args.out,
            townrecord_python=args.townrecord_python,
            roster_path=args.roster,
            agenda_path=args.agenda,
            window_s=args.window_minutes * 60.0,
            model=args.model,
            reuse_draft=args.reuse_draft,
            diarize=args.diarize,
            textflowkit_python=args.textflowkit_python,
            granicus_host=args.granicus,
            granicus_view_id=args.view_id,
        )
    except (
        capture_mod.CaptureError,
        granicus_mod.GranicusError,
        PipelineError,
        llm.ModelError,
        OSError,
    ) as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief.meeting")
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser("run", help="capture, speakers and recap for one video")
    run_cmd.add_argument(
        "video_id", help="the YouTube video id (e.g. uHFuCfd5MVQ), or with --granicus the clip id"
    )
    run_cmd.add_argument(
        "--granicus", metavar="HOST", help="Granicus host, e.g. oakland.granicus.com"
    )
    run_cmd.add_argument(
        "--view-id", type=int, help="Granicus ViewPublisher view_id that lists the clip"
    )
    run_cmd.add_argument("--out", type=Path, required=True, help="output folder")
    run_cmd.add_argument("--agenda", type=Path, help="official agenda text file (see agenda.py)")
    run_cmd.add_argument("--roster", type=Path, help="roster JSON (default: the Longmont roster)")
    run_cmd.add_argument(
        "--townrecord-python",
        help="TownRecord's python.exe (default: $CIVICCAST_TOWNRECORD_PYTHON or the scratch venv)",
    )
    run_cmd.add_argument(
        "--window-minutes",
        type=float,
        default=DEFAULT_WINDOW_S / 60,
        help="minutes of transcript per model call",
    )
    run_cmd.add_argument("--model", default=llm.DEFAULT_MODEL)
    run_cmd.add_argument(
        "--reuse-draft",
        action="store_true",
        help="use the saved draft.json instead of calling the model again",
    )
    run_cmd.add_argument(
        "--diarize",
        action="store_true",
        help="label caption lines with voices using textflowkit's diarizer (needs the audio)",
    )
    run_cmd.add_argument(
        "--textflowkit-python",
        help="textflowkit's python.exe (default: $CIVICCAST_TEXTFLOWKIT_PYTHON or its venv)",
    )
    run_cmd.set_defaults(run=_run)

    args = parser.parse_args(argv)
    return args.run(args)
