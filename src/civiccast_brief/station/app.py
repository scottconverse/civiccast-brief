"""The always-on app: the desk at ``/desk`` and the weekly job on its schedule.

    python -m civiccast_brief.station serve --config station.json [--port 8765]

``station.json`` (paths are relative to its own folder; ``{python}`` is this Python)::

    {
      "db": "desk.db",
      "city": "cities/longmont",
      "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
      "job": [
        ["{python}", "-m", "civiccast_brief.weekly", "run", "--city", "cities/longmont", ...]
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from civiccast_brief.review.store import ReviewError, ReviewStore
from civiccast_brief.review.web import BIND_HOST, DEFAULT_PORT, make_desk_app, serve
from civiccast_brief.station.runner import Schedule, Station, StationError

DESK_ROOT = "/desk"


@dataclass(frozen=True)
class StationConfig:
    db: Path
    city: Path | None
    schedule: Schedule
    job: list[list[str]]
    folder: Path


def load_config(path: Path) -> StationConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StationError(f"cannot read {path}: {exc.strerror or exc}") from None
    except ValueError as exc:
        raise StationError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise StationError(f"{path} must hold a JSON object")
    folder = path.resolve().parent

    def place(value: Any, name: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise StationError(f'"{name}" must be a path')
        return (folder / value).resolve()

    when = data.get("schedule")
    if not isinstance(when, dict):
        raise StationError('"schedule" must say the day, time and timezone')
    schedule = Schedule.parse(
        str(when.get("day", "")), str(when.get("time", "")), str(when.get("timezone", ""))
    )
    job = data.get("job")
    if (
        not isinstance(job, list)
        or not job
        or not all(isinstance(s, list) and s and all(isinstance(p, str) for p in s) for s in job)
    ):
        raise StationError('"job" must be a list of commands, each a list of words')
    city = place(data["city"], "city") if data.get("city") else None
    return StationConfig(place(data.get("db"), "db"), city, schedule, job, folder)


def make_station_app(store: ReviewStore, station: Station, city_dir: Path | None = None) -> FastAPI:
    """The desk mounted at /desk with the Runs page, and the scheduler running while it is up."""
    desk = make_desk_app(store, city_dir=city_dir, root=DESK_ROOT, station=station)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        station.start()
        try:
            yield
        finally:
            station.stop()

    app = FastAPI(
        title="CivicCast Brief", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.desk = desk.state.desk
    app.state.station = station

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(DESK_ROOT + "/", status_code=303)

    app.mount(DESK_ROOT, desk)
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief.station")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("serve", help="run the desk and the weekly job's schedule")
    run.add_argument("--config", type=Path, required=True, help="station.json")
    run.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"(default {DEFAULT_PORT})")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if config.city is not None and not config.city.is_dir():
            raise StationError(f"the city folder {config.city} does not exist")
        with ReviewStore(config.db) as store:
            station = Station(config.db, config.job, config.schedule, cwd=config.folder)
            app = make_station_app(store, station, config.city)
            print(
                f"CivicCast Brief: http://{BIND_HOST}:{args.port}{DESK_ROOT}/  "
                f"(weekly job: {config.schedule.describe()}; Ctrl+C to stop)",
                flush=True,
            )
            serve(app, args.port)
    except (StationError, ReviewError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
