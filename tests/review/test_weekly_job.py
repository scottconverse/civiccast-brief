"""The plain desk's weekly job: weekly.json, the job command, and the Weekly job page."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from desk_signin import signed_in
from fastapi.testclient import TestClient

from civiccast_brief.review import weekly_job
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app
from civiccast_brief.station.app import make_station_app
from civiccast_brief.station.runner import Schedule, Station

DENVER = ZoneInfo("America/Denver")
MONDAY_6 = Schedule.parse("monday", "06:00", "America/Denver")
AT_SLOT = datetime(2026, 11, 16, 6, 0, tzinfo=DENVER)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class Steps:
    """Stands in for the job's command: records each run, never runs a real command."""

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.argvs: list[list[str]] = []

    def __call__(self, argv, cwd, output) -> int:
        self.ran.append(argv[0])
        self.argvs.append(list(argv))
        output(f"{argv[0]} says hello")
        return 0


def test_a_missing_file_gives_the_defaults_with_the_schedule_off(tmp_path):
    job = weekly_job.load(tmp_path)
    assert job == weekly_job.DEFAULT
    assert (job.enabled, job.day, job.at, job.zone, job.days, job.diarize) == (
        False,
        "monday",
        "06:00",
        "America/Denver",
        14,
        False,
    )


def test_save_and_load_round_trip(tmp_path):
    job = weekly_job.WeeklyJob(
        enabled=True, day="tuesday", at="07:30", zone="America/Chicago", days=21, diarize=True
    )
    path = weekly_job.save(tmp_path, job)
    assert path == tmp_path / "weekly.json"
    assert json.loads(path.read_text(encoding="utf-8"))["schedule"]["day"] == "tuesday"
    assert weekly_job.load(tmp_path) == job
    assert weekly_job.load(tmp_path).schedule == Schedule.parse(
        "tuesday", "07:30", "America/Chicago"
    )


def test_a_bad_setting_is_refused_and_nothing_is_written(tmp_path):
    for bad, field in (
        (weekly_job.WeeklyJob(day="someday"), "day"),
        (weekly_job.WeeklyJob(at="6am"), "time"),
        (weekly_job.WeeklyJob(zone="Mars/Olympus"), "timezone"),
        (weekly_job.WeeklyJob(days=0), "days"),
        (weekly_job.WeeklyJob(days=61), "days"),
    ):
        with pytest.raises(weekly_job.WeeklyJobError) as caught:
            weekly_job.save(tmp_path, bad)
        assert caught.value.field == field
        assert not (tmp_path / "weekly.json").exists()


def test_the_job_is_one_weekly_run_command_with_diarize_when_asked(tmp_path):
    plain = weekly_job.WeeklyJob(days=14)
    steps = weekly_job.job_steps(plain, tmp_path / "city", tmp_path / "desk.db", tmp_path / "work")
    assert steps == [
        [
            sys.executable,
            "-m",
            "civiccast_brief.weekly",
            "run",
            "--city",
            str(tmp_path / "city"),
            "--work",
            str(tmp_path / "work"),
            "--db",
            str(tmp_path / "desk.db"),
            "--days",
            "14",
        ]
    ]
    voiced = weekly_job.job_steps(
        weekly_job.WeeklyJob(days=7, diarize=True), tmp_path / "city", "desk.db", "work"
    )
    assert voiced[0][-1] == "--diarize"
    assert voiced[0][-3:-1] == ["--days", "7"]
    assert not any(part in voiced[0] for part in ("--episodes", "--site-out", "--base-url"))
    assert "--voices" not in voiced[0]  # the city has no voices.json


def test_the_job_passes_the_city_s_voices_file_when_it_has_one(tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    (city / "voices.json").write_text("{}", encoding="utf-8")
    steps = weekly_job.job_steps(weekly_job.DEFAULT, city, "desk.db", "work")
    assert steps[0][-2:] == ["--voices", str(city / "voices.json")]


def test_the_schedule_off_means_no_scheduled_run_but_run_now_still_works(tmp_path):
    clock = Clock(AT_SLOT)
    steps = Steps()
    station = Station(
        tmp_path / "desk.db",
        [["find"]],
        MONDAY_6,
        clock=clock,
        runner=steps,
        scheduled=False,
    )
    assert station.tick() is None  # the slot is due, but the schedule is off
    assert steps.ran == []
    started = station.run_now()
    assert started is not None
    station.join_workers()
    assert steps.ran == ["find"]
    assert station.status().scheduled is False
    station.reconfigure(scheduled=True)
    assert station.status().scheduled is True


@pytest.fixture
def plain(tmp_path):
    """The plain desk with a city folder, a schedule-off station and no real command."""
    city = tmp_path / "city"
    city.mkdir()
    clock = Clock(datetime(2026, 11, 11, 12, 0, tzinfo=DENVER))
    steps = Steps()
    work = tmp_path / "runs" / "weekly"
    work.mkdir(parents=True)
    with ReviewStore(tmp_path / "desk.db") as store:
        station = Station(
            store.path,
            [["find"]],
            MONDAY_6,
            cwd=tmp_path,
            runner=steps,
            clock=clock,
            scheduled=False,
        )
        app = make_desk_app(
            store,
            city_dir=city,
            station=station,
            plain_weekly=True,
            weekly_work=work,
        )
        yield app, station, steps, city


def test_the_plain_desk_shows_the_schedule_form_and_what_the_job_runs(plain):
    app, _, _, _ = plain
    client = signed_in(app)
    page = client.get("/runs").text
    assert 'href="/runs"' in page  # the nav link
    assert 'action="/runs/schedule"' in page
    assert "Run every week" in page and "Days back" in page
    assert 'value="14"' in page
    assert "America/Denver" in page
    assert "What the job runs" in page
    assert str(Path("runs") / "weekly") in page  # the work folder the job passes as --work
    assert "civiccast_brief.weekly" in page
    assert 'data-busy="Saving…"' in page
    assert 'data-busy="Starting…"' in page


def test_saving_the_schedule_writes_weekly_json_and_updates_the_station(plain):
    app, station, _, city = plain
    client = signed_in(app)
    reply = client.post(
        "/runs/schedule",
        data={
            "token": app.state.desk.token,
            "enabled": "1",
            "day": "tuesday",
            "time": "07:30",
            "timezone": "America/Chicago",
            "days": "21",
            "diarize": "1",
        },
        follow_redirects=False,
    )
    assert reply.status_code == 303
    assert reply.headers["location"] == "/runs?notice=saved"
    saved = weekly_job.load(city)
    assert (saved.enabled, saved.day, saved.at, saved.zone, saved.days, saved.diarize) == (
        True,
        "tuesday",
        "07:30",
        "America/Chicago",
        21,
        True,
    )
    assert station.scheduled is True
    assert station.schedule.describe() == "Tuesdays at 07:30 (America/Chicago)"
    assert station.steps[0][-1] == "--diarize"
    page = client.get("/runs?notice=saved").text
    assert "Saved. Next run: Tue 2026-11-17 07:30 CST." in page


def test_saving_a_bad_schedule_shows_the_error_and_saves_nothing(plain):
    app, station, _, city = plain
    client = signed_in(app)
    reply = client.post(
        "/runs/schedule",
        data={
            "token": app.state.desk.token,
            "day": "monday",
            "time": "6am",
            "timezone": "America/Denver",
            "days": "14",
        },
    )
    assert reply.status_code == 400
    assert "Not saved:" in reply.text
    assert 'aria-invalid="true"' in reply.text
    assert 'class="field-error"' in reply.text
    assert not (city / "weekly.json").exists()
    assert station.schedule == MONDAY_6


def test_saving_with_the_schedule_off_says_to_use_run_now(plain):
    app, station, _, _ = plain
    client = signed_in(app)
    client.post(
        "/runs/schedule",
        data={
            "token": app.state.desk.token,
            "day": "monday",
            "time": "06:00",
            "timezone": "America/Denver",
            "days": "14",
        },
        follow_redirects=False,
    )
    assert station.scheduled is False
    assert "Saved. The schedule is off; use Run now." in client.get("/runs?notice=saved").text


def test_without_a_city_the_page_says_the_weekly_job_needs_one(tmp_path):
    with ReviewStore(tmp_path / "desk.db") as store:
        client = signed_in(make_desk_app(store, plain_weekly=True))
        page = client.get("/runs")
        assert page.status_code == 200
        assert "needs the desk started with a city folder" in page.text
        assert 'action="/runs/schedule"' not in page.text
        assert 'action="/runs/test"' not in page.text


def test_in_the_station_the_schedule_is_read_only(tmp_path):
    with ReviewStore(tmp_path / "desk.db") as store:
        station = Station(
            store.path,
            [["find"]],
            MONDAY_6,
            runner=Steps(),
            clock=Clock(datetime(2026, 11, 11, 12, 0, tzinfo=DENVER)),
        )
        app = make_station_app(store, station)
        client = signed_in(app, "/desk")
        page = client.get("/desk/runs")
        assert "Set in station.json." in page.text
        assert "Mondays at 06:00 (America/Denver)" in page.text
        assert 'action="/desk/runs/schedule"' not in page.text
        with TestClient(app, base_url="https://127.0.0.1"):
            pass


def test_test_one_meeting_runs_the_limit_command_and_shows_its_run(plain):
    app, station, steps, _ = plain
    client = signed_in(app)

    reply = client.post("/runs/test", data={"token": app.state.desk.token}, follow_redirects=False)

    assert reply.status_code == 303
    assert reply.headers["location"].startswith("/runs/")
    run_id = int(reply.headers["location"].rsplit("/", 1)[1])
    station.join_workers()
    assert steps.argvs and steps.argvs[-1][-2:] == ["--limit", "1"]
    assert "civiccast_brief.weekly" in steps.argvs[-1]
    assert steps.argvs[-1][0] == sys.executable
    run = station.run(run_id)
    assert run is not None
    assert (run.trigger, run.status) == ("test", "ok")
    page = client.get(reply.headers["location"]).text
    assert f"Run {run_id}" in page
    assert "Test" in page


def test_the_weekly_job_page_shows_the_test_section_above_the_schedule(plain):
    app, _, _, _ = plain
    page = signed_in(app).get("/runs").text
    assert 'action="/runs/test"' in page
    assert "Test one meeting" in page
    assert 'data-busy="Starting…"' in page
    assert page.index("Test one meeting") < page.index('action="/runs/schedule"')
    assert "the last 14 days" in page  # the weeks-long window, not a literal N
    assert "last N days" not in page


def test_the_weekly_job_page_says_what_test_one_meeting_publishes(plain):
    app, _, _, _ = plain
    page = signed_in(app).get("/runs").text
    assert "Nothing is published" not in page
    assert "go to the website with the next publish" in page


def test_test_one_meeting_without_a_city_starts_nothing(tmp_path):
    clock = Clock(datetime(2026, 11, 11, 12, 0, tzinfo=DENVER))
    steps = Steps()
    with ReviewStore(tmp_path / "desk.db") as store:
        station = Station(
            store.path,
            [["find"]],
            MONDAY_6,
            runner=steps,
            clock=clock,
            scheduled=False,
        )
        app = make_desk_app(
            store, station=station, plain_weekly=True, weekly_work=tmp_path / "work"
        )
        client = signed_in(app)
        reply = client.post(
            "/runs/test", data={"token": app.state.desk.token}, follow_redirects=False
        )
    assert reply.status_code == 400
    assert "needs the desk started with a city folder" in reply.text
    assert steps.ran == []


def test_the_items_page_says_the_schedule_is_off_and_shows_no_next_run(plain):
    app, _, _, _ = plain
    page = signed_in(app).get("/").text
    assert "schedule off" in page
    assert "next run" not in page


def test_the_items_page_shows_the_next_run_when_the_schedule_is_on(plain):
    app, station, _, _ = plain
    station.reconfigure(scheduled=True)
    page = signed_in(app).get("/").text
    assert "next run" in page
    assert "schedule off" not in page
