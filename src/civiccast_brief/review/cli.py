"""Command line for the editor desk.

    python -m civiccast_brief.review serve --db PATH [--city DIR] [--port 8765]
    python -m civiccast_brief.review export --db PATH --out DIR

serve runs the editor desk on 127.0.0.1 only. It creates an empty database if PATH does not
exist, and moves an older review database to the desk's states (approved items become
published). --city names the city folder, where the desk keeps models.json and
sources.json; without it the Settings and Sources pages are read-only notes.

export writes published items to DIR; it never creates a database, and it reports each
skipped item and the reason.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from civiccast_brief.review import weekly_job
from civiccast_brief.review.export import export_published
from civiccast_brief.review.store import ReviewError, ReviewStore
from civiccast_brief.review.web import BIND_HOST, DEFAULT_PORT, make_desk_app, serve
from civiccast_brief.review.writers import desk_writer
from civiccast_brief.station.runner import Station, StationError


def _port(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}") from exc
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"port out of range: {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m civiccast_brief.review",
        description="Editor desk: the local desk, and the export of published items.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("serve", help="run the editor desk on 127.0.0.1")
    run.add_argument("--db", required=True, type=Path, help="SQLite desk database")
    run.add_argument("--city", type=Path, help="city folder (models.json, sources.json)")
    run.add_argument(
        "--work",
        type=Path,
        help="the weekly run's work folder; connects Redraft to the story and script writers",
    )
    run.add_argument(
        "--port", type=_port, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})"
    )

    export = commands.add_parser("export", help="write published items to a folder")
    export.add_argument("--db", required=True, type=Path, help="SQLite desk database")
    export.add_argument(
        "--out", required=True, type=Path, help="output folder (created if missing)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args.db, args.city, args.port, args.work)
    return _export(args.db, args.out)


def _serve(db: Path, city: Path | None, port: int, work: Path | None = None) -> int:
    if city is not None and not city.is_dir():
        print(f"error: the city folder {city} does not exist", file=sys.stderr)
        return 1
    if work is not None and not work.is_dir():
        print(f"error: the work folder {work} does not exist", file=sys.stderr)
        return 1
    if work is not None and city is None:
        print("error: --work needs --city, which picks the writers' models", file=sys.stderr)
        return 1
    writer = desk_writer(work, city) if work is not None else None
    try:
        weekly_work = None
        settings = None
        if city is not None:
            settings = weekly_job.DEFAULT
            try:
                settings = weekly_job.load(city)
            except weekly_job.WeeklyJobError as exc:
                # A broken weekly.json must not keep the desk from starting: use the defaults
                # with the schedule off, and let the Weekly job page show what is wrong.
                print(
                    f"warning: {exc}; starting with the schedule off",
                    file=sys.stderr,
                )
            weekly_work = work if work is not None else db.parent / "runs" / "weekly"
            weekly_work.mkdir(parents=True, exist_ok=True)
        with ReviewStore(db) as store:
            if store.migrated:
                print(f"moved {store.migrated} items from the old review screen to the desk")
            station = None
            if settings is not None and city is not None and weekly_work is not None:
                station = Station(
                    store.path,
                    weekly_job.job_steps(settings, city, store.path, weekly_work),
                    settings.schedule,
                    cwd=db.parent,
                    scheduled=settings.enabled,
                )
            app = make_desk_app(
                store,
                city_dir=city,
                writer=writer,
                station=station,
                plain_weekly=True,
                weekly_work=weekly_work,
            )
            print(f"editor desk: http://{BIND_HOST}:{port}/  (Ctrl+C to stop)", flush=True)
            serve(app, port)
    except (ReviewError, weekly_job.WeeklyJobError, StationError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _export(db: Path, out: Path) -> int:
    try:
        result = export_published(db, out)
    except (ReviewError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for folder in result.exported:
        print(f"exported: {folder}")
    for skip in result.skipped:
        print(f"skipped item {skip.item_id} ({skip.meeting_id}): {skip.reason}")
    print(f"{len(result.exported)} exported, {len(result.skipped)} skipped")
    return 0
