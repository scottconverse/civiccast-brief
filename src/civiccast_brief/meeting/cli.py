"""Command line for the Phase 4 meeting pipeline.

    python -m civiccast_brief.meeting run <youtube_video_id> --out DIR
    python -m civiccast_brief.meeting run <clip_id> --granicus HOST --view-id N --out DIR
    python -m civiccast_brief.meeting run <video_or_clip_id> --city CITY_DIR --out DIR
        [--agenda AGENDA.txt] [--documents documents.json] [--roster ROSTER.json] [--ytdlp-python PATH]
        [--window-minutes 30] [--model NAME] [--reuse-draft]

With --city and no --model, the recap and the judge use the city's model picks (models.json).

Writes transcript.json, speakers.json and recap.json (plus draft.json, verify-report.json and
summary.json) under DIR. Exit 0 when every stage finished; 1 when capture or the pipeline failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from civiccast_brief import models
from civiccast_brief.city import CityError, load_city
from civiccast_brief.meeting import cablecast as cablecast_mod
from civiccast_brief.meeting import capture as capture_mod
from civiccast_brief.meeting import granicus as granicus_mod
from civiccast_brief.meeting.pipeline import Complete, PipelineError, run
from civiccast_brief.meeting.recap import DEFAULT_WINDOW_S
from civiccast_brief.script import llm


def _city_defaults(args: argparse.Namespace) -> None:
    """Fill the roster and the Granicus settings from --city, unless given on the command line."""
    if not args.city:
        return
    city = load_city(args.city)
    args.roster = args.roster or city.roster_path
    if city.platform == "cablecast" and not args.cablecast:
        args.cablecast = city.cablecast_host
    if city.platform == "granicus" and not args.granicus:
        args.granicus = city.granicus_host
        args.view_id = args.view_id if args.view_id is not None else city.granicus_view_id


def _roster_body(path: Path | None) -> str:
    """The body the roster is for. Only that field is read here: the pipeline checks the
    members, and an empty roster is reported there."""
    if path is None:
        return ""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    body = data.get("body") if isinstance(data, dict) else None
    return body if isinstance(body, str) else ""


def _writers(args: argparse.Namespace) -> tuple[str, Complete | None, Complete | None]:
    """The recap and judge writers. ``--model`` is an Ollama model for both; without it a
    ``--city`` run takes the city's picks (models.json) for the roster's body."""
    if args.model or not args.city:
        return args.model or llm.DEFAULT_MODEL, None, None
    body = _roster_body(args.roster)
    settings = models.load_settings(args.city)
    picks = f"recap={settings.pick('recap', body)} judge={settings.pick('judge', body)}"
    return (
        picks,
        _logged(models.complete_for(args.city, "recap", body)),
        _logged(models.complete_for(args.city, "judge", body)),
    )


def _logged(inner: Complete) -> Complete:
    def complete(prompt: str) -> tuple[str, object]:
        text, receipt = inner(prompt)
        print(f"model: {receipt.line() if hasattr(receipt, 'line') else receipt}", file=sys.stderr)
        return text, receipt

    return complete


def _run(args: argparse.Namespace) -> int:
    try:
        _city_defaults(args)
        model, recap_writer, judge_writer = _writers(args)
        summary = run(
            args.video_id,
            args.out,
            ytdlp_python=args.ytdlp_python,
            roster_path=args.roster,
            agenda_path=args.agenda,
            documents_path=args.documents,
            window_s=args.window_minutes * 60.0,
            model=model,
            complete=recap_writer,
            judge_complete=judge_writer,
            reuse_draft=args.reuse_draft,
            diarize=args.diarize,
            granicus_host=args.granicus,
            granicus_view_id=args.view_id,
            cablecast_host=args.cablecast,
        )
    except (
        capture_mod.CaptureError,
        granicus_mod.GranicusError,
        cablecast_mod.CablecastError,
        CityError,
        PipelineError,
        llm.ModelError,
        models.ModelCallError,
        models.ModelSettingsError,
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
        "video_id",
        help="the YouTube video id (e.g. uHFuCfd5MVQ), or with --granicus the clip id, or with --cablecast the show id",
    )
    run_cmd.add_argument(
        "--granicus", metavar="HOST", help="Granicus host, e.g. oakland.granicus.com"
    )
    run_cmd.add_argument(
        "--view-id", type=int, help="Granicus ViewPublisher view_id that lists the clip"
    )
    run_cmd.add_argument(
        "--cablecast",
        metavar="HOST",
        help="Cablecast host, e.g. reflect-yourcity.cablecast.tv; the video id is the show id",
    )
    run_cmd.add_argument("--out", type=Path, required=True, help="output folder")
    run_cmd.add_argument("--agenda", type=Path, help="official agenda text file (see agenda.py)")
    run_cmd.add_argument(
        "--documents",
        type=Path,
        help="the meeting's documents.json (packet and minutes text); names a voice that "
        "introduces itself by a name printed there",
    )
    run_cmd.add_argument(
        "--city", type=Path, help="city folder from `python -m civiccast_brief setup`"
    )
    run_cmd.add_argument("--roster", type=Path, help="roster JSON (default: the --city roster)")
    run_cmd.add_argument(
        "--ytdlp-python",
        help="the python.exe that runs yt-dlp (default: CivicCast Brief's private yt-dlp runtime)",
    )
    run_cmd.add_argument(
        "--window-minutes",
        type=float,
        default=DEFAULT_WINDOW_S / 60,
        help="minutes of transcript per model call",
    )
    run_cmd.add_argument(
        "--model",
        help="an Ollama model for the recap and the judge (default: the --city picks in "
        f"models.json, or {llm.DEFAULT_MODEL})",
    )
    run_cmd.add_argument(
        "--reuse-draft",
        action="store_true",
        help="use the saved draft.json instead of calling the model again",
    )
    run_cmd.add_argument(
        "--diarize",
        action="store_true",
        help="label caption lines with voices (needs the audio and the diarize extra)",
    )
    run_cmd.set_defaults(run=_run)

    args = parser.parse_args(argv)
    return args.run(args)
