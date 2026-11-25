"""``run-due``: every city when it is due, one at a time, with fake steps only."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from civiccast_brief.review import run_due as run_due_module
from civiccast_brief.review.cli import build_parser
from civiccast_brief.review.run_due import run_due
from civiccast_brief.station.runner import Station

ZONE = ZoneInfo("America/Denver")


def make_city(cities: Path, slug: str, *, weekly: dict | None = None) -> None:
    (cities / slug / "city").mkdir(parents=True, exist_ok=True)
    (cities / slug / "runs" / "weekly").mkdir(parents=True, exist_ok=True)
    if weekly is not None:
        (cities / slug / "city" / "weekly.json").write_text(json.dumps(weekly), encoding="utf-8")


def on(day: str, at: str) -> dict:
    return {
        "enabled": True,
        "schedule": {"day": day, "time": at, "timezone": "America/Denver"},
        "days": 14,
        "diarize": False,
    }


class FakeRunner:
    """Records the steps run; fails the commands whose cwd's city is in ``fail``."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.fail = fail or set()

    def __call__(self, argv, cwd, output) -> int:
        self.calls.append((list(argv), Path(cwd).name))
        output("ran " + " ".join(argv))
        return 1 if Path(cwd).name in self.fail else 0


def station_factory(
    now: list[datetime], runner: FakeRunner, *, later: int = 7, busy: set[str] = frozenset()
):
    """Build a real Station with a clock the test moves after the Station is set up.

    The Station records ``since`` at the hour it is built (05:00), then the clock moves to
    ``later``, so a 06:00 slot is due and a 09:00 slot is not."""

    def build(db, city_dir, *, weekly_work):
        now[0] = now[0].replace(hour=5, minute=0)
        station = Station(
            db,
            [["fake-step"]],
            run_due_module.weekly_job.load(city_dir).schedule,
            cwd=Path(db).parent,
            clock=lambda: now[0],
            runner=runner,
        )
        now[0] = now[0].replace(hour=later, minute=0)
        if Path(db).parent.name in busy:
            station._begin("manual")  # what another run looks like while it is going
        return station

    return build


def test_a_due_city_runs_and_a_city_not_due_is_skipped(tmp_path) -> None:
    cities = tmp_path / "cities"
    make_city(cities, "alpha", weekly=on("monday", "06:00"))
    make_city(cities, "bravo", weekly=on("monday", "09:00"))
    now = [datetime(2026, 11, 23, 5, 0, tzinfo=ZONE)]  # a Monday
    runner = FakeRunner()
    lines: list[str] = []
    build = station_factory(now, runner)

    run_due(cities, out=lines.append, station_for=build)

    assert lines == ["alpha: ran (ok)", "bravo: not due"]
    assert [city for _, city in runner.calls] == ["alpha"]


def test_a_city_with_a_run_already_going_is_busy(tmp_path) -> None:
    cities = tmp_path / "cities"
    make_city(cities, "alpha", weekly=on("monday", "06:00"))
    now = [datetime(2026, 11, 23, 5, 0, tzinfo=ZONE)]
    runner = FakeRunner()
    lines: list[str] = []

    run_due(
        cities,
        out=lines.append,
        station_for=station_factory(now, runner, busy={"alpha"}),
    )

    assert lines == ["alpha: busy (another run is going)"]
    assert runner.calls == []


def test_a_broken_weekly_json_is_reported_and_skipped(tmp_path) -> None:
    cities = tmp_path / "cities"
    make_city(cities, "alpha")
    (cities / "alpha" / "city" / "weekly.json").write_text("{not json", encoding="utf-8")
    lines: list[str] = []
    called: list[str] = []

    run_due(
        cities,
        out=lines.append,
        station_for=lambda *a, **k: called.append("built"),
    )

    assert len(lines) == 1
    assert lines[0].startswith("alpha: broken weekly.json:")
    assert called == []


def test_a_schedule_that_is_off_is_not_run(tmp_path) -> None:
    cities = tmp_path / "cities"
    off = on("monday", "06:00")
    off["enabled"] = False
    make_city(cities, "alpha", weekly=off)
    lines: list[str] = []

    run_due(cities, out=lines.append, station_for=lambda *a, **k: None)

    assert lines == ["alpha: schedule off"]


def test_one_city_failing_does_not_stop_the_rest(tmp_path) -> None:
    cities = tmp_path / "cities"
    make_city(cities, "alpha", weekly=on("monday", "06:00"))
    make_city(cities, "bravo", weekly=on("monday", "06:00"))
    now = [datetime(2026, 11, 23, 5, 0, tzinfo=ZONE)]
    runner = FakeRunner(fail={"alpha"})
    lines: list[str] = []

    run_due(cities, out=lines.append, station_for=station_factory(now, runner))

    assert lines == ["alpha: ran (failed)", "bravo: ran (ok)"]
    assert sorted(city for _, city in runner.calls) == ["alpha", "bravo"]


def test_left_out_and_badly_named_folders_are_ignored(tmp_path) -> None:
    cities = tmp_path / "cities"
    make_city(cities, "alpha", weekly=on("monday", "06:00"))
    make_city(cities / "_left-out", "ghost", weekly=on("monday", "06:00"))
    (cities / "Not A Slug" / "city").mkdir(parents=True)
    now = [datetime(2026, 11, 23, 5, 0, tzinfo=ZONE)]
    runner = FakeRunner()
    lines: list[str] = []

    run_due(cities, out=lines.append, station_for=station_factory(now, runner))

    assert lines == ["alpha: ran (ok)"]
    assert [city for _, city in runner.calls] == ["alpha"]


def test_the_command_line_takes_a_cities_folder() -> None:
    parsed = build_parser().parse_args(["run-due", "--cities", "cities"])
    assert parsed.cities == Path("cities")
    assert parsed.command == "run-due"
