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
from civiccast_brief.station.runner import (
    PUBLISH_DELAY_SECONDS,
    PUBLISH_RETRIES_MAX,
    PUBLISH_RETRY_SECONDS,
    Schedule,
    Station,
    StationError,
)

DENVER = ZoneInfo("America/Denver")
MONDAY_6 = Schedule.parse("monday", "06:00", "America/Denver")
WED_NOON = datetime(2026, 11, 11, 12, 0, tzinfo=DENVER)
DELAY = timedelta(seconds=PUBLISH_DELAY_SECONDS)
RETRY = timedelta(seconds=PUBLISH_RETRY_SECONDS)


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


def test_set_publish_replaces_the_steps_and_turns_publishing_on(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps, publish=())
    assert s.publish_steps == []
    s.request_publish()  # with no publish steps there is nothing to queue
    assert s.status().publish_at is None

    s.set_publish([["build-site"], ["deploy-site"]])
    assert s.publish_steps == [["build-site"], ["deploy-site"]]
    s.request_publish()
    assert s.status().publish_at == WED_NOON + DELAY
    clock.now = WED_NOON + DELAY
    assert s.tick() is not None
    assert steps.ran == ["build-site", "deploy-site"]


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


def test_a_failed_publish_keeps_the_request_queued_and_is_retried_after_thirty_minutes(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps, publish=(["build"], ["deploy"]))
    s.request_publish()
    clock.now += DELAY
    steps.codes = {"deploy": 1}
    first = s.tick()
    assert first is not None
    assert (s.run(first).status, s.run(first).exit_code) == ("failed", 1)
    # The failed run did not drop the desk's click, but it is not retried at once either.
    assert s.status().publish_at == clock.now + RETRY

    steps.ran.clear()
    steps.codes = {}
    clock.now += RETRY - timedelta(seconds=1)
    assert s.tick() is None  # not before the 30 minutes

    clock.now += timedelta(seconds=1)
    retry = s.tick()
    assert retry is not None
    assert steps.ran == ["build", "deploy"]
    assert s.status().publish_at is None  # a publish that went through covers it


def test_a_publish_that_keeps_failing_is_dropped_after_three_retries(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps, publish=(["build"], ["deploy"]))
    s.request_publish()
    steps.codes = {"deploy": 1}
    attempts = 0
    while True:
        clock.now += DELAY if attempts == 0 else RETRY
        run_id = s.tick()
        if run_id is None:
            break
        attempts += 1
        assert s.run(run_id).status == "failed"

    # The click's first try and three retries, then the request is gone.
    assert attempts == 1 + PUBLISH_RETRIES_MAX
    assert s.status().publish_at is None
    assert "dropping the request" in s.runs()[0].log


def test_a_scheduled_run_that_publishes_covers_a_future_dated_retry(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    s.request_publish()
    steps.codes = {"deploy": 1}
    clock.now += DELAY
    s.tick()  # the desk's try fails, so a retry waits thirty minutes out
    assert s.status().publish_at == clock.now + RETRY
    assert s._retry_count() == 1

    # A scheduled run reaches the publish steps before the retry is due. Its publish covers the
    # queued request, so nothing is left to publish again.
    steps.codes = {}
    s.run_once("schedule")
    assert s.status().publish_at is None
    assert s._retry_count() == 0


def test_scheduled_runs_that_keep_failing_count_and_drop_a_future_dated_retry(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    s.request_publish()
    steps.codes = {"deploy": 1}
    clock.now += DELAY
    s.tick()  # the desk's try fails: one retry queued, one failure counted
    assert s._retry_count() == 1
    assert s.status().publish_at is not None

    # Each scheduled run that fails counts as a failed try of the queued request, rather than
    # looking like a fresh desk click that resets the count.
    s.run_once("schedule")
    assert s._retry_count() == 2
    s.run_once("schedule")
    assert s._retry_count() == 3
    s.run_once("schedule")  # the third retry fails: at the cap, the request is dropped
    assert s._retry_count() == 0
    assert s.status().publish_at is None
    assert "publish failed 4 times in a row" in s.runs()[0].log


def test_a_desk_click_during_a_failing_run_survives_at_the_normal_delay(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    s.request_publish()
    steps.codes = {"deploy": 1}
    clock.now += DELAY
    s.tick()  # fails; a retry waits thirty minutes out
    assert s.status().publish_at == clock.now + RETRY

    def click_while_deploying() -> None:
        clock.now += timedelta(seconds=5)
        s.request_publish()

    steps.during["deploy"] = click_while_deploying
    s.run_once("schedule")  # fails too, but the click made during it is not part of it
    steps.during.clear()
    assert s.status().publish_at == clock.now + DELAY
    assert s._retry_count() == 0


def test_the_drop_message_names_the_true_number_of_failed_tries(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps)
    s.request_publish()
    steps.codes = {"deploy": 1}
    clock.now += DELAY
    s.tick()
    for _ in range(PUBLISH_RETRIES_MAX):
        clock.now += RETRY
        if s.tick() is None:
            break
    assert s.status().publish_at is None
    assert "publish failed 4 times in a row" in s.runs()[0].log


def test_a_new_desk_click_after_failures_publishes_at_the_normal_delay(tmp_path):
    clock = Clock(WED_NOON)
    steps = Steps()
    s = station(tmp_path, clock, steps, publish=(["build"], ["deploy"]))
    s.request_publish()
    steps.codes = {"deploy": 1}
    clock.now += DELAY
    s.tick()
    clock.now += RETRY
    s.tick()  # one retry has now failed too
    assert s.status().publish_at == clock.now + RETRY

    # A fresh click resets the count and goes out at the normal delay, not 30 minutes out.
    steps.ran.clear()
    steps.codes = {}
    clock.now += timedelta(seconds=5)
    s.request_publish()
    assert s.status().publish_at == clock.now + DELAY
    assert s.tick() is None
    clock.now += DELAY
    run_id = s.tick()
    assert run_id is not None and s.run(run_id).status == "ok"
    assert steps.ran == ["build", "deploy"]
    assert s.status().publish_at is None


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


def test_the_control_room_shows_a_failed_run_and_an_edit_queues_a_publish(desk):
    client, store, s, clock = desk
    token = client.app.state.desk.token
    s.runner.codes["make"] = 4
    s.run_now()
    s.join_workers()  # the run is a background thread; wait for it before reading the page
    page = client.get("/desk/").text
    assert "Weekly job: last run" in page and "failed" in page
    assert "The last run failed (code 4)" in page

    item = _item(store, "m1")
    store.publish(item.id)
    clock.now += DELAY
    s.tick()
    assert s.status().publish_at is None
    saved = client.post(
        f"/desk/items/{item.id}/save",
        data={
            "token": token,
            "recap": "Recap, fixed.",
            "script": "[S1] Script, fixed.",
            "speaker:voice 01": "unknown",
        },
    )
    assert "Saved and published again" in saved.text
    assert "The site updates in about a minute." in saved.text
    assert s.status().publish_at == clock.now + DELAY
    client.post(f"/desk/items/{item.id}/unpublish", data={"token": token})
    assert store.get_item(item.id).status == "held"
