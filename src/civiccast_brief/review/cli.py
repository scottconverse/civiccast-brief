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

from fastapi import FastAPI

from civiccast_brief.review.export import export_published
from civiccast_brief.review.hub import make_hub_app
from civiccast_brief.review.store import ReviewError, ReviewStore
from civiccast_brief.review.web import (
    BIND_HOST,
    DEFAULT_PORT,
    city_desk,
    make_desk_app,
    serve,
)
from civiccast_brief.review.writers import desk_writer
from civiccast_brief.station.runner import StationError


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
        "--cities",
        type=Path,
        help="a folder of cities, each holding city/ and desk.db; one desk, several cities",
    )
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
        return _serve(args.db, args.city, args.port, args.work, args.cities)
    return _export(args.db, args.out)


def _serve(
    db: Path,
    city: Path | None,
    port: int,
    work: Path | None = None,
    cities: Path | None = None,
) -> int:
    if cities is not None:
        if city is not None or work is not None:
            print(
                "error: --cities cannot be used with --city or --work; each city in the "
                "folder has its own city folder and work folder",
                file=sys.stderr,
            )
            return 1
        if cities.exists() and not cities.is_dir():
            print(f"error: {cities} is not a folder", file=sys.stderr)
            return 1
    else:
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
        if cities is not None:
            cities.mkdir(parents=True, exist_ok=True)
            app = make_hub_app(db, cities)
            print(f"editor desk: http://{BIND_HOST}:{port}/  (Ctrl+C to stop)", flush=True)
            serve(app, port)
            return 0
        weekly_work = None
        if city is not None:
            weekly_work = work if work is not None else db.parent / "runs" / "weekly"
        with ReviewStore(db) as store:
            if store.migrated:
                print(f"moved {store.migrated} items from the old review screen to the desk")
            app: FastAPI
            if city is not None and weekly_work is not None:
                app, _ = city_desk(store, city, weekly_work=weekly_work, writer=writer)
            else:
                app = make_desk_app(
                    store,
                    city_dir=city,
                    writer=writer,
                    plain_weekly=True,
                    weekly_work=None,
                )
            print(f"editor desk: http://{BIND_HOST}:{port}/  (Ctrl+C to stop)", flush=True)
            serve(app, port)
    except (ReviewError, StationError, OSError, sqlite3.Error) as exc:
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
