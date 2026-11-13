"""The always-on station: the desk at /desk and the weekly job on its schedule."""

from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from desk_signin import signed_in
from fastapi.testclient import TestClient

from civiccast_brief.review.store import ReviewStore
from civiccast_brief.station.app import load_config, make_station_app
from civiccast_brief.station.runner import Schedule, Station, StationError

DENVER = ZoneInfo("America/Denver")
MONDAY_6 = Schedule.parse("monday", "06:00", "America/Denver")


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class Steps:
    """Stands in for the job's commands: records each, says what it was told to."""

    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.ran: list[str] = []
        self.codes = codes or {}
        self.gate: threading.Event | None = None

    def __call__(self, argv, cwd, output) -> int:
        name = argv[0]
        self.ran.append(name)
        output(f"{name} says hello")
        if self.gate is not None:
            assert self.gate.wait(10)
        return self.codes.get(name, 0)


def at(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=DENVER)


def station(tmp_path, clock, steps, job=(["find"], ["publish"])) -> Station:
    return Station(
        tmp_path / "desk.db", [list(s) for s in job], MONDAY_6, clock=clock, runner=steps
    )


def test_the_schedule_is_a_weekly_slot_in_the_city_s_own_time_zone():
    # Wednesday 2026-11-11: the last slot was Monday the 9th at 06:00 MST, the next the 16th.
    wed = at(2026, 11, 11, 12, 0)
    assert MONDAY_6.last_at_or_before(wed) == at(2026, 11, 9, 6, 0)
    assert MONDAY_6.next_after(wed) == at(2026, 11, 16, 6, 0)
    # On the slot itself, that slot is the last one; the next is a week on.
    assert MONDAY_6.last_at_or_before(at(2026, 11, 9, 6, 0)) == at(2026, 11, 9, 6, 0)
    # Across the clock change (1 Nov 2026) the job still starts at 06:00 local, not 05:00.
    assert MONDAY_6.next_after(at(2026, 10, 28, 9, 0)) == at(2026, 11, 2, 6, 0)
    assert MONDAY_6.next_after(at(2026, 10, 28, 9, 0)).utcoffset() == timedelta(hours=-7)
    assert MONDAY_6.describe() == "Mondays at 06:00 (America/Denver)"
    for bad in (("someday", "06:00", "America/Denver"), ("monday", "6am", "America/Denver")):
        with pytest.raises(StationError):
            Schedule.parse(*bad)
    with pytest.raises(StationError):
        Schedule.parse("monday", "06:00", "Mars/Olympus")


def test_a_new_station_waits_for_its_first_slot_then_runs_once_per_slot(tmp_path):
    clock = Clock(at(2026, 11, 11, 12, 0))
    steps = Steps()
    s = station(tmp_path, clock, steps)
    assert s.tick() is None  # Monday's slot was before the station existed
    clock.now = at(2026, 11, 16, 5, 59)
    assert s.tick() is None
    clock.now = at(2026, 11, 16, 6, 0)
    first = s.tick()
    assert first is not None
    assert steps.ran == ["find", "publish"]
    clock.now = at(2026, 11, 16, 6, 30)
    assert s.tick() is None  # this slot has had its run
    run = s.run(first)
    assert run is not None
    assert (run.status, run.trigger, run.exit_code) == ("ok", "schedule", 0)
    assert "> find" in run.log and "publish says hello" in run.log


def test_a_slot_missed_while_the_station_was_off_runs_once_when_it_comes_back(tmp_path):
    clock = Clock(at(2026, 11, 11, 12, 0))
    steps = Steps()
    station(tmp_path, clock, steps)
    clock.now = at(2026, 11, 25, 9, 0)  # off for two Mondays
    back = station(tmp_path, clock, steps)  # the same database, a new process
    assert back.tick() is not None
    assert back.tick() is None
    assert steps.ran == ["find", "publish"]


def test_run_now_never_overlaps_a_run_that_is_going(tmp_path):
    clock = Clock(at(2026, 11, 16, 6, 0))
    steps = Steps()
    steps.gate = threading.Event()
    s = station(tmp_path, clock, steps)
    first = s.run_now()
    assert first is not None
    assert s.run_now() is None
    clock.now = at(2026, 11, 23, 6, 0)  # the schedule comes round while it is still going
    assert s.tick() is None
    status = s.status()
    assert status.running is not None and status.running.id == first
    steps.gate.set()
    s.join_workers()
    assert s.status().running is None
    assert s.status().last is not None and s.status().last.status == "ok"
    assert s.run_now() is not None
    s.join_workers()


def test_a_failing_step_stops_the_job_and_the_run_says_so(tmp_path):
    clock = Clock(at(2026, 11, 16, 6, 0))
    steps = Steps({"find": 3})
    s = station(tmp_path, clock, steps)
    run = s.run(s.run_once("manual") or 0)
    assert steps.ran == ["find"]  # publish never ran
    assert run is not None
    assert (run.status, run.exit_code) == ("failed", 3)
    assert "stopped: that step ended with code 3" in run.log


def test_a_run_cut_off_by_a_restart_is_marked_interrupted(tmp_path):
    clock = Clock(at(2026, 11, 16, 6, 0))
    steps = Steps()
    steps.gate = threading.Event()
    s = station(tmp_path, clock, steps)
    cut = s.run_now()
    assert cut is not None
    again = station(tmp_path, clock, Steps())  # the station starts again mid-run
    assert again.run(cut).status == "interrupted"
    assert again.run_now() is not None  # and the lock is free
    again.join_workers()
    steps.gate.set()
    s.join_workers()


def test_the_job_runs_real_commands_and_keeps_their_output(tmp_path):
    clock = Clock(at(2026, 11, 16, 6, 0))
    s = Station(
        tmp_path / "desk.db",
        [
            ["{python}", "-c", "print('found 3 meetings')"],
            ["{python}", "-c", "raise SystemExit(2)"],
        ],
        MONDAY_6,
        cwd=tmp_path,
        clock=clock,
    )
    assert s.steps[0][0] == sys.executable
    run = s.run(s.run_once("manual") or 0)
    assert run is not None
    assert "found 3 meetings" in run.log
    assert (run.status, run.exit_code) == ("failed", 2)
    missing = Station(tmp_path / "d2.db", [["no-such-program-ccb"]], MONDAY_6, clock=clock)
    gone = missing.run(missing.run_once("manual") or 0)
    assert gone is not None and gone.status == "failed"
    assert "could not start no-such-program-ccb" in gone.log


@pytest.fixture
def app(tmp_path):
    clock = Clock(at(2026, 11, 11, 12, 0))
    steps = Steps()
    with ReviewStore(tmp_path / "desk.db") as store:
        s = station(tmp_path, clock, steps)
        yield make_station_app(store, s), s, steps


def test_the_desk_lives_at_desk_and_every_link_stays_under_it(app):
    application, _, _ = app
    anon = TestClient(application, base_url="https://127.0.0.1")
    home = anon.get("/", follow_redirects=False)
    assert (home.status_code, home.headers["location"]) == (303, "/desk/")
    locked = anon.get("/desk/", follow_redirects=False)
    assert (locked.status_code, locked.headers["location"]) == (303, "/desk/sign-in")
    assert anon.post("/desk/city", data={}).text == "Sign in first: /desk/sign-in\n"
    client = signed_in(application, "/desk")
    for page in ("/desk/", "/desk/settings", "/desk/sources", "/desk/roster", "/desk/runs"):
        reply = client.get(page)
        assert reply.status_code == 200, page
        links = re.findall(r'(?:href|action|src)="(/[^"]*)"', reply.text)
        assert links, page
        assert all(link.startswith("/desk/") for link in links), (page, links)
    saved = client.post("/desk/sign-out", data={"token": application.state.desk.token})
    assert saved.url.path == "/desk/sign-in"


def test_the_weekly_job_page_shows_last_run_next_run_and_run_now_works(app):
    application, s, steps = app
    # The machine's clock gives a bare offset; the page names the city's zone, as Next run does.
    s.clock = lambda: datetime(2026, 11, 13, 21, 39, tzinfo=ZoneInfo("UTC"))
    client = signed_in(application, "/desk")
    page = client.get("/desk/runs").text
    assert 'href="/desk/runs"' in page  # the nav link
    assert "Mondays at 06:00 (America/Denver)" in page
    assert "None yet" in page
    assert "Mon 2026-11-16 06:00 MST" in page  # the next run
    reply = client.post(
        "/desk/runs/now", data={"token": application.state.desk.token}, follow_redirects=False
    )
    assert reply.headers["location"] == "/desk/runs?notice=started"
    s.join_workers()
    assert steps.ran == ["find", "publish"]
    page = client.get("/desk/runs?notice=started").text
    assert "The weekly job started." in page
    run_id = s.runs()[0].id
    assert f'href="/desk/runs/{run_id}"' in page
    assert '<span class="status ok">ok</span>' in page
    assert "Fri 2026-11-13 14:39 MST" in page
    assert "UTC" not in page
    output = client.get(f"/desk/runs/{run_id}").text
    assert "find says hello" in output
    assert client.get("/desk/runs/999").status_code == 404
    refused = client.post("/desk/runs/now", data={"token": "wrong"})
    assert refused.status_code == 403
    assert len(s.runs()) == 1


def test_a_going_run_disables_run_now_and_the_page_keeps_itself_fresh(app):
    application, s, steps = app
    steps.gate = threading.Event()
    client = signed_in(application, "/desk")
    token = application.state.desk.token
    client.post("/desk/runs/now", data={"token": token})
    busy = client.post("/desk/runs/now", data={"token": token}, follow_redirects=False)
    assert busy.headers["location"] == "/desk/runs?notice=busy"
    page = client.get("/desk/runs?notice=busy").text
    assert "A run is already going." in page
    assert '<meta http-equiv="refresh" content="5">' in page
    assert re.search(r"<button[^>]*disabled[^>]*>Run now</button>", page)
    steps.gate.set()
    s.join_workers()


def test_the_plain_desk_has_no_weekly_job_page(tmp_path):
    from civiccast_brief.review.web import make_desk_app

    with ReviewStore(tmp_path / "d.db") as store:
        client = signed_in(make_desk_app(store))
        assert "Weekly job" not in client.get("/").text
        assert client.get("/runs").status_code == 404


def test_station_json_paths_are_read_from_its_own_folder(tmp_path):
    folder = tmp_path / "longmont"
    folder.mkdir()
    config = folder / "station.json"
    config.write_text(
        json.dumps(
            {
                "db": "desk.db",
                "city": "cities/longmont",
                "schedule": {"day": "Monday", "time": "06:00", "timezone": "America/Denver"},
                "job": [["{python}", "-m", "civiccast_brief.weekly", "run"]],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_config(config)
    assert loaded.db == (folder / "desk.db").resolve()
    assert loaded.city == (folder / "cities" / "longmont").resolve()
    assert loaded.schedule == MONDAY_6
    for broken, says in (
        ({"db": "d.db", "schedule": {}, "job": [["x"]]}, "day must be"),
        ({"db": "d.db", "schedule": MONDAY_JSON, "job": []}, '"job" must be'),
        ({"db": "d.db", "schedule": MONDAY_JSON, "job": [["x", 1]]}, '"job" must be'),
        ({"schedule": MONDAY_JSON, "job": [["x"]]}, '"db" must be'),
    ):
        config.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(StationError, match=says):
            load_config(config)
    config.write_text("{not json", encoding="utf-8")
    with pytest.raises(StationError, match="not valid JSON"):
        load_config(config)


MONDAY_JSON = {"day": "monday", "time": "06:00", "timezone": "America/Denver"}


def test_the_scheduler_runs_while_the_app_is_up(app):
    application, s, _ = app
    with TestClient(application, base_url="https://127.0.0.1"):
        assert s._thread is not None and s._thread.is_alive()
        thread = s._thread
    assert s._thread is None
    assert not thread.is_alive()
