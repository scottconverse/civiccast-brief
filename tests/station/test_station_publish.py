"""Publish is immediate: the station's publish steps, its publish queue and the desk's part."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from desk_signin import signed_in

from civiccast_brief.review.store import ReviewStore
from civiccast_brief.station.app import load_config, make_station_app
from civiccast_brief.station.runner import PUBLISH_DELAY_SECONDS, Schedule, Station, StationError

DENVER = ZoneInfo("America/Denver")
MONDAY_6 = Schedule.parse("monday", "06:00", "America/Denver")
WED_NOON = datetime(2026, 11, 11, 12, 0, tzinfo=DENVER)
DELAY = timedelta(seconds=PUBLISH_DELAY_SECONDS)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class Steps:
    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.ran: list[str] = []
        self.codes = codes or {}
        self.during: dict[str, object] = {}

    def __call__(self, argv, cwd, output) -> int:
        name = argv[0]
        self.ran.append(name)
        output(f"{name} says hello")
        hook = self.during.get(name)
        if callable(hook):
            hook()
        return self.codes.get(name, 0)


def station(tmp_path, clock, steps, publish=(["build"], ["deploy"])) -> Station:
    return Station(
        tmp_path / "desk.db",
        [["find"], ["make"]],
        MONDAY_6,
        clock=clock,
        runner=steps,
        publish=[list(p) for p in publish],
    )


def test_the_publish_steps_follow_the_job_and_run_even_when_the_job_fails(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    s.run_once("manual")
    assert steps.ran == ["find", "make", "build", "deploy"]
    assert s.runs()[0].status == "ok"

    # The week's meetings fail; what the desk already published still goes out, and the run
    # says it failed.
    steps.ran.clear()
    steps.codes = {"find": 3}
    s.run_once("manual")
    assert steps.ran == ["find", "build", "deploy"]
    run = s.runs()[0]
    assert (run.status, run.exit_code) == ("failed", 3)
    assert "publishing what the desk already published:" in run.log

    # A failing deploy fails the run too.
    steps.ran.clear()
    steps.codes = {"build": 2}
    s.run_once("manual")
    assert steps.ran == ["find", "make", "build"]
    assert (s.runs()[0].status, s.runs()[0].exit_code) == ("failed", 2)


def test_a_desk_publish_queues_one_publish_run_that_starts_half_a_minute_later(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    assert s.status().publish_at is None
    s.request_publish()
    clock.now += timedelta(seconds=10)
    s.request_publish()  # a second click joins the first
    assert s.status().publish_at == WED_NOON + DELAY
    assert s.tick() is None  # not yet
    clock.now = WED_NOON + DELAY
    run_id = s.tick()
    assert run_id is not None
    assert steps.ran == ["build", "deploy"]  # the publish steps only
    assert s.run(run_id).trigger == "publish"
    assert s.status().publish_at is None
    assert s.tick() is None  # one run for both clicks


def test_a_full_run_covers_the_requests_made_before_its_publish_steps(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    s.request_publish()

    def click_while_deploying() -> None:
        clock.now += timedelta(seconds=5)
        s.request_publish()

    steps.during["deploy"] = click_while_deploying
    s.run_once("manual")
    # The request from before the run was published by it; the one made while it deployed
    # (after the site was built) is still queued.
    queued = s.status().publish_at
    assert queued == WED_NOON + timedelta(seconds=5) + DELAY
    steps.ran.clear()
    steps.during.clear()
    clock.now = queued
    s.tick()
    assert steps.ran == ["build", "deploy"]
    assert s.status().publish_at is None


def test_a_queued_publish_waits_for_a_going_run_and_survives_a_restart(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    s.request_publish()
    clock.now += DELAY
    gate, started = threading.Event(), threading.Event()
    steps.during["find"] = lambda: (started.set(), gate.wait(10))
    worker = threading.Thread(target=s.run_once, args=("manual",))
    worker.start()
    assert started.wait(10)
    assert s.tick() is None  # a run is going; the publish does not overlap it
    gate.set()
    worker.join(10)

    s.request_publish()
    again = station(tmp_path, clock, Steps())
    assert again.status().publish_at == clock.now + DELAY


def test_without_publish_steps_a_request_does_nothing(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps, publish=())
    s.request_publish()
    clock.now += DELAY
    assert s.status().publish_at is None
    assert s.tick() is None
    assert steps.ran == []


@pytest.fixture
def desk(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    with ReviewStore(tmp_path / "desk.db") as store:
        s = station(tmp_path, clock, steps)
        app = make_station_app(store, s)
        yield signed_in(app, "/desk"), store, s, clock


def _item(store, meeting_id: str):
    return store.add_item(
        meeting_id=meeting_id,
        title="Council night",
        meeting_date="2026-11-04",
        body="City Council",
        speakers={"voice 01": "unknown"},
        recap="Recap.",
        script="[S1] Script.",
    )


def test_publishing_or_killing_a_public_item_queues_a_publish_and_the_page_says_so(desk):
    client, store, s, clock = desk
    token = client.app.state.desk.token
    draft = _item(store, "m1")

    # Holding an item nobody can see changes nothing public.
    client.post(f"/desk/items/{draft.id}/hold", data={"token": token, "note": "later"})
    assert s.status().publish_at is None

    client.post(f"/desk/items/{draft.id}/publish", data={"token": token})
    assert s.status().publish_at == WED_NOON + DELAY
    page = client.get("/desk/runs").text
    assert "Queued" in page
    assert "Wed 2026-11-11 12:00 MST" in page
    assert '<meta http-equiv="refresh" content="5">' in page

    clock.now += DELAY
    s.tick()
    page = client.get("/desk/runs").text
    assert "Queued" not in page
    assert "<td>Publish</td>" in page

    # Killing a published item takes it off the site, so it queues a publish too.
    client.post(f"/desk/items/{draft.id}/kill", data={"token": token, "note": "wrong"})
    assert s.status().publish_at == clock.now + DELAY


def test_station_json_takes_publish_commands(tmp_path):
    config = tmp_path / "station.json"
    base = {
        "db": "desk.db",
        "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
        "job": [["{python}", "-c", "pass"]],
    }
    config.write_text(json.dumps(base), encoding="utf-8")
    assert load_config(config).publish == []
    config.write_text(json.dumps({**base, "publish": [["{python}", "-V"]]}), encoding="utf-8")
    assert load_config(config).publish == [["{python}", "-V"]]
    for bad in ("deploy", [["ok"], []], [[1]]):
        config.write_text(json.dumps({**base, "publish": bad}), encoding="utf-8")
        with pytest.raises(StationError, match='"publish" must be a list of commands'):
            load_config(config)
