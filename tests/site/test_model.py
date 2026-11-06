"""Input model: what loads, what is left out, and what is refused with a clear message."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from site_support import FIXTURES

from civiccast_brief.site.model import InputError, load_site_input
from civiccast_brief.site.timecode import clock, iso_duration, parse_seconds, timecode, vtt_time


def _episode(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(directory: Path, name: str, data: dict) -> None:
    (directory / name).write_text(json.dumps(data), encoding="utf-8")


def test_fixture_loads_approved_episodes_newest_first_and_excludes_unapproved() -> None:
    site = load_site_input(FIXTURES)
    assert [e.id for e in site.episodes] == [
        "2026-10-20-example-council",
        "2026-10-06-example-sample",
    ]
    assert site.excluded == (("2026-09-15-example-unapproved", "not approved"),)
    council = site.episodes[0]
    assert council.claims[1].seconds == 28 * 60
    assert council.chapters[1].start == 40
    assert council.transcript is not None
    assert council.transcript[1].speaker == "Mayor"
    assert council.transcript[0].speaker == "unknown"
    assert council.corrections[0].note.startswith("An earlier version")
    sample = site.episodes[1]
    assert sample.sample is True
    assert sample.display_headline.startswith("SAMPLE: ")
    assert sample.transcript is None
    assert sample.claims == ()


def test_city_settings_are_read(episodes_dir: Path) -> None:
    site = load_site_input(episodes_dir)
    assert site.city.slug == "example"
    assert site.city.hosts == {"S1": "Maya", "S2": "Theo"}
    assert site.city.podcast.category == "Government"
    assert site.city.disclaimer.startswith("This episode is AI-generated")


def test_script_tags_must_name_a_speaker(episodes_dir: Path) -> None:
    path = episodes_dir / "ep-2026-10-06-example-sample.json"
    data = _episode(path)
    data["script"] = "[S1] Fine.\nNo tag on this line."
    _write(episodes_dir, path.name, data)
    with pytest.raises(InputError) as caught:
        load_site_input(episodes_dir)
    assert any("line 2 must start with a speaker tag" in p for p in caught.value.problems)


def test_every_problem_is_reported_together(episodes_dir: Path) -> None:
    path = episodes_dir / "ep-2026-10-20-example-council.json"
    data = copy.deepcopy(_episode(path))
    data["published"] = "2026-10-21T09:00:00"  # no timezone
    data["meeting_date"] = "20 October 2026"
    data["audio"]["bytes"] = 0
    data["recap"]["claims"][1]["timestamp"] = "0:99:00"
    data["video_url"] = "javascript:alert(1)"
    _write(episodes_dir, path.name, data)
    with pytest.raises(InputError) as caught:
        load_site_input(episodes_dir)
    problems = "\n".join(caught.value.problems)
    assert "published must include a timezone offset" in problems
    assert "meeting_date must be a date" in problems
    assert "bytes must be greater than zero" in problems
    assert "minutes or seconds above 59" in problems
    assert "video_url must be an http or https address" in problems


def test_podcast_owner_email_is_required(episodes_dir: Path) -> None:
    city_path = episodes_dir / "city.json"
    city = json.loads(city_path.read_text(encoding="utf-8"))
    del city["podcast"]["owner_email"]
    city_path.write_text(json.dumps(city), encoding="utf-8")
    with pytest.raises(InputError, match="owner_email is required"):
        load_site_input(episodes_dir)


def test_missing_city_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "ep.json").write_text("{}", encoding="utf-8")
    with pytest.raises(InputError, match="city.json is required"):
        load_site_input(tmp_path)


def test_episode_must_match_the_city(episodes_dir: Path) -> None:
    path = episodes_dir / "ep-2026-10-06-example-sample.json"
    data = _episode(path)
    data["city"] = "elsewhere"
    _write(episodes_dir, path.name, data)
    with pytest.raises(InputError, match="does not match city.json"):
        load_site_input(episodes_dir)


def test_duplicate_episode_ids_are_refused(episodes_dir: Path) -> None:
    source = _episode(episodes_dir / "ep-2026-10-06-example-sample.json")
    _write(episodes_dir, "copy-of-sample.json", source)
    with pytest.raises(InputError, match="is also used by"):
        load_site_input(episodes_dir)


def test_approval_needs_a_name(episodes_dir: Path) -> None:
    path = episodes_dir / "ep-2026-10-06-example-sample.json"
    data = _episode(path)
    del data["approved_by"]
    _write(episodes_dir, path.name, data)
    with pytest.raises(InputError, match="approved_by is required"):
        load_site_input(episodes_dir)


def test_invalid_json_is_reported_with_its_file(episodes_dir: Path) -> None:
    (episodes_dir / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError, match="broken.json: not valid JSON"):
        load_site_input(episodes_dir)


def test_artwork_must_stay_inside_the_episodes_folder(episodes_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")
    city_path = episodes_dir / "city.json"
    city = json.loads(city_path.read_text(encoding="utf-8"))
    city["artwork"] = "../outside.png"
    city_path.write_text(json.dumps(city), encoding="utf-8")
    with pytest.raises(InputError, match="must stay inside|must point to a PNG"):
        load_site_input(episodes_dir)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("0:28:00", 1680.0), ("28:00", 1680.0), ("1:21:06.5", 4866.5), (12, 12.0), ("12.5", 12.5)],
)
def test_timecodes_parse(value: object, seconds: float) -> None:
    assert parse_seconds(value) == seconds


@pytest.mark.parametrize("value", ["1:99:00", "abc", True, -1, "1:2:3:4", "99:00:00:00"])
def test_bad_timecodes_are_refused(value: object) -> None:
    with pytest.raises(ValueError):
        parse_seconds(value)


def test_time_formats() -> None:
    assert clock(327.4) == "5:27"
    assert clock(3725) == "1:02:05"
    assert timecode(5 * 3600 + 34 * 60 + 45) == "5:34:45"
    assert vtt_time(3.14159) == "00:00:03.142"
    assert iso_duration(327.4) == "PT5M27S"
    assert iso_duration(3725) == "PT1H2M5S"
