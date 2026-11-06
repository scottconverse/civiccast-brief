"""Command line for the producer review package.

    python -m civiccast_brief.review serve --db PATH [--port 8765]
    python -m civiccast_brief.review export --db PATH --out DIR

serve runs the producer screen on 127.0.0.1 only. It creates an empty database if PATH does not
exist. export writes approved items to DIR; it never creates a database, and it reports each
skipped item and the reason.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from civiccast_brief.review.export import export_approved
from civiccast_brief.review.store import ReviewError, ReviewStore
from civiccast_brief.review.web import DEFAULT_PORT, make_review_server


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
        description="Producer review: the local screen, and the export of approved items.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the producer screen on 127.0.0.1")
    serve.add_argument("--db", required=True, type=Path, help="SQLite review database")
    serve.add_argument(
        "--port", type=_port, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})"
    )

    export = commands.add_parser("export", help="write approved items to a folder")
    export.add_argument("--db", required=True, type=Path, help="SQLite review database")
    export.add_argument(
        "--out", required=True, type=Path, help="output folder (created if missing)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args.db, args.port)
    return _export(args.db, args.out)


def _serve(db: Path, port: int) -> int:
    try:
        with ReviewStore(db) as store:
            server = make_review_server(store, port)
            host, bound_port = server.server_address[:2]
            print(f"producer screen: http://{host}:{bound_port}/  (Ctrl+C to stop)")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print()
            finally:
                server.server_close()
    except (ReviewError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _export(db: Path, out: Path) -> int:
    try:
        result = export_approved(db, out)
    except (ReviewError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for folder in result.exported:
        print(f"exported: {folder}")
    for skip in result.skipped:
        print(f"skipped item {skip.item_id} ({skip.meeting_id}): {skip.reason}")
    print(f"{len(result.exported)} exported, {len(result.skipped)} skipped")
    return 0
