"""A Cablecast source in the weekly run: the shows found in the week, and the meeting command."""

import json
from datetime import date
from pathlib import Path

from civiccast_brief.meeting import cablecast
from civiccast_brief.sources import Source
from civiccast_brief.weekly.find import Found, Listers, find_meetings
from civiccast_brief.weekly.run import Settings, Steps, run_week

HOST = "reflect-cityofloveland-co.cablecast.tv"
FIXTURES = Path(__file__).parent.parent / "meeting" / "fixtures" / "cablecast"
COUNCIL = "City Council"
PC = "Planning Commission"


def _shows():
    data = json.loads((FIXTURES / "shows.json").read_text(encoding="utf-8"))
    return cablecast.parse_shows(data, HOST)


def _source(*bodies):
    return Source(kind="cablecast", bodies=bodies, host=HOST)


def test_the_weeks_shows_of_the_source_bodies_are_found_with_their_host():
    asked = []
    listers = Listers(cablecast=lambda host: asked.append(host) or _shows())
    found, problems = find_meetings(
        [_source(COUNCIL, PC)], date(2026, 10, 27), date(2026, 11, 10), listers
    )
    assert problems == []
    assert asked == [HOST]
    assert [(m.key, m.body, m.day, m.cablecast_host, m.reason) for m in found] == [
        (f"cablecast:{HOST}/744", COUNCIL, date(2026, 10, 27), HOST, ""),
        (f"cablecast:{HOST}/746", COUNCIL, date(2026, 11, 4), HOST, ""),
        (f"cablecast:{HOST}/748", PC, date(2026, 11, 9), HOST, ""),
    ]
    assert found[1].title == "City Council Meeting November 4, 2026"
    assert found[1].video == "746"


def test_a_show_that_names_no_body_of_the_source_is_not_a_meeting_of_it():
    listers = Listers(cablecast=lambda host: _shows())
    found, _ = find_meetings([_source(COUNCIL)], date(2026, 11, 5), date(2026, 11, 10), listers)
    # 748 is a Planning Commission meeting and 749 a LURA meeting; 750 names no "City Council".
    assert found == []


def test_a_cablecast_source_with_no_reader_is_reported():
    found, problems = find_meetings(
        [_source(COUNCIL)], date(2026, 10, 27), date(2026, 11, 10), Listers()
    )
    assert found == []
    assert problems == [f"cablecast:{HOST}: no reader for cablecast sources"]


def test_a_failing_cablecast_listing_is_a_problem_not_a_crash():
    def broken(host):
        raise cablecast.CablecastError("could not fetch: HTTP Error 503")

    found, problems = find_meetings(
        [_source(COUNCIL)], date(2026, 10, 27), date(2026, 11, 10), Listers(cablecast=broken)
    )
    assert found == []
    assert problems == [f"cablecast:{HOST}: could not fetch: HTTP Error 503"]


def test_the_meeting_command_gets_the_cablecast_host_and_the_show_id(tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    (city / "roster.json").write_text(json.dumps({"body": COUNCIL}), encoding="utf-8")
    calls = []

    def meeting(argv):
        calls.append(list(argv))
        return 1  # the run stops here; only the command line is looked at

    def unused(argv):
        raise AssertionError(argv)

    steps = Steps(
        meeting=meeting,
        script=unused,
        episode=unused,
        article=unused,
        review=unused,
        audio=unused,
        site=unused,
    )
    settings = Settings(city=city, work=tmp_path / "work", db=tmp_path / "desk.db", site=None)
    meeting_found = Found(
        COUNCIL,
        date(2026, 11, 4),
        "City Council Meeting November 4, 2026",
        f"cablecast:{HOST}",
        "746",
        cablecast_host=HOST,
    )
    run_week(settings, [meeting_found], date(2026, 11, 10), steps)

    (argv,) = calls
    assert argv[:2] == ["run", "746"]
    assert argv[argv.index("--cablecast") + 1] == HOST
    assert "--granicus" not in argv
