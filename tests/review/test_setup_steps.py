"""The first-run checklist: each step from the file or run record, and the Items page panel."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from desk_signin import signed_in

from civiccast_brief import models, sources
from civiccast_brief.review import setup_steps, voices, weekly_job
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app
from civiccast_brief.station.runner import Schedule, Station

DENVER = ZoneInfo("America/Denver")
SCHEDULE = Schedule.parse("monday", "06:00", "America/Denver")


@pytest.fixture
def city(tmp_path):
    folder = tmp_path / "city"
    folder.mkdir()
    return folder


def make_station(tmp_path, *, code: int = 0) -> Station:
    """A station whose job is one fake command that ends with ``code``; it never runs anything."""

    def runner(argv, cwd, output):
        output(f"{argv[0]} ran")
        return code

    return Station(
        tmp_path / "station.db",
        [["find"]],
        SCHEDULE,
        clock=lambda: datetime(2026, 11, 16, 6, 0, tzinfo=DENVER),
        runner=runner,
        scheduled=False,
    )


def by_key(city_dir, station=None) -> dict[str, setup_steps.Step]:
    return {step.key: step for step in setup_steps.steps(city_dir, station)}


def done(city_dir, key: str, station=None) -> bool:
    return by_key(city_dir, station)[key].done


def write_city(city: Path) -> None:
    (city / "city.json").write_text(
        json.dumps({"name": "Longmont", "slug": "longmont", "state": "CO"}),
        encoding="utf-8",
    )


def write_sources(city: Path) -> None:
    sources.save_sources(
        city,
        [sources.Source("youtube", ("City Council",), channel_id="UCabcdefghijklmnopqrstuv")],
    )


def write_check(city: Path) -> None:
    (city / "city-check.json").write_text(
        json.dumps({"checked": "2026-11-20", "results": []}), encoding="utf-8"
    )


def write_roster(city: Path) -> None:
    (city / "roster.json").write_text(
        json.dumps({"body": "City Council", "members": []}), encoding="utf-8"
    )


def test_an_empty_city_has_every_step_to_do(city):
    steps = setup_steps.steps(city, None)
    assert [step.key for step in steps] == [
        "city",
        "sources",
        "check",
        "roster",
        "models",
        "voices",
        "test",
        "weekly",
    ]
    assert all(not step.done for step in steps)
    assert sum(1 for step in steps if not step.optional) == 6
    assert [step.href for step in steps if step.key == "city"] == ["/city"]


def test_city_json_with_a_name_and_state_ticks_the_city_step(city):
    assert not done(city, "city")
    write_city(city)
    assert done(city, "city")


def test_sources_json_with_one_source_ticks_the_sources_step(city):
    assert not done(city, "sources")
    write_sources(city)
    assert done(city, "sources")


def test_the_saved_city_check_ticks_the_check_step(city):
    assert not done(city, "check")
    write_check(city)
    assert done(city, "check")


def test_a_loaded_roster_ticks_the_roster_step(city):
    assert not done(city, "roster")
    write_roster(city)
    assert done(city, "roster")


def test_models_and_voices_are_optional_and_tick_when_present(city):
    steps = by_key(city)
    assert steps["models"].optional and not steps["models"].done
    assert steps["voices"].optional and not steps["voices"].done
    models.save_settings(city, models.ModelSettings())
    voices.save(city, voices.Voices(engine="gemini"))
    steps = by_key(city)
    assert steps["models"].done and steps["voices"].done


def test_a_finished_test_run_ticks_the_test_step(tmp_path, city):
    station = make_station(tmp_path)
    assert not done(city, "test", station)
    station.run_once("test")
    assert done(city, "test", station)


def test_a_failed_test_run_does_not_tick_the_test_step(tmp_path, city):
    station = make_station(tmp_path, code=1)
    station.run_once("test")
    assert not done(city, "test", station)


def test_weekly_json_with_enabled_true_ticks_the_weekly_step(city):
    assert not done(city, "weekly")
    weekly_job.save(city, weekly_job.WeeklyJob(enabled=True))
    assert done(city, "weekly")


def test_bad_json_in_a_file_is_not_done_and_does_not_crash(city, tmp_path):
    for name in ("city.json", "sources.json", "city-check.json", "roster.json"):
        (city / name).write_text("{not json", encoding="utf-8")
    (city / "models.json").write_text("{not json", encoding="utf-8")
    (city / "voices.json").write_text("{not json", encoding="utf-8")
    (city / "weekly.json").write_text("{not json", encoding="utf-8")
    steps = by_key(city, make_station(tmp_path))
    assert not any(step.done for step in steps.values())


def test_no_city_gives_one_start_step():
    steps = setup_steps.steps(None, None)
    assert len(steps) == 1
    step = steps[0]
    assert step.title == "Start the desk with a city folder"
    assert not step.done and not step.optional and step.href == ""
    assert "desk.cmd" in step.hint and "review serve --db PATH --city" in step.hint


def test_the_items_page_shows_the_panel_until_the_required_steps_are_done(tmp_path, city):
    with ReviewStore(tmp_path / "desk.db") as store:
        station = make_station(tmp_path)
        client = signed_in(make_desk_app(store, city_dir=city, station=station))
        page = client.get("/")
        assert page.status_code == 200
        assert "Set up your city" in page.text
        assert "0 of 6 required steps done." in page.text
        assert "To do" in page.text

        write_city(city)
        write_sources(city)
        write_check(city)
        write_roster(city)
        station.run_once("test")
        weekly_job.save(city, weekly_job.WeeklyJob(enabled=True))
        # Optional steps stay undone; they must not keep the panel up.
        page = client.get("/")
        assert "Set up" not in page.text
        assert "required steps done" not in page.text


def test_the_items_page_names_the_city_once_it_has_one(tmp_path, city):
    write_city(city)
    with ReviewStore(tmp_path / "desk.db") as store:
        client = signed_in(make_desk_app(store, city_dir=city))
        assert "Set up Longmont" in client.get("/").text
