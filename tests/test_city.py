import json

import pytest

from civiccast_brief import __main__ as top
from civiccast_brief.city import CityError, load_city, setup
from civiccast_brief.meeting import cli as meeting_cli
from civiccast_brief.meeting.roster import load_roster
from civiccast_brief.site.model import load_site_input

ARGS = {
    "slug": "san-mateo-county",
    "name": "San Mateo County",
    "state": "California",
    "body": "Board of Supervisors",
    "contact_email": "corrections@example.org",
    "podcast_email": "podcast@example.org",
}


def test_setup_writes_a_city_the_site_build_reads(tmp_path):
    folder = tmp_path / "smc"
    written = setup(folder, roster_page="https://example.org/board", **ARGS)
    assert [p.name for p in written] == ["city.json", "roster.json"]
    site = load_site_input(folder)  # roster.json is not read as an episode
    assert site.city.slug == "san-mateo-county" and site.episodes == ()
    roster = json.loads((folder / "roster.json").read_text(encoding="utf-8"))
    assert roster["members"] == [] and roster["sources"] == ["https://example.org/board"]
    with pytest.raises(ValueError, match="roster has no members"):
        load_roster(folder / "roster.json")


def test_setup_never_overwrites(tmp_path):
    setup(tmp_path, **ARGS)
    with pytest.raises(CityError, match="not overwritten"):
        setup(tmp_path, **ARGS)


@pytest.mark.parametrize(
    ("change", "message"),
    [({"slug": "San Mateo"}, "slug"), ({"granicus_host": "x.granicus.com"}, "view-id")],
)
def test_setup_refuses_bad_settings(tmp_path, change, message):
    with pytest.raises(CityError, match=message):
        setup(tmp_path, **{**ARGS, **change})
    assert not (tmp_path / "city.json").exists()


def test_granicus_city_settings_reach_the_meeting_run(tmp_path, monkeypatch):
    setup(tmp_path, granicus_host="sanmateocounty.granicus.com", granicus_view_id=1, **ARGS)
    city = load_city(tmp_path)
    assert (city.platform, city.granicus_host, city.granicus_view_id) == (
        "granicus",
        "sanmateocounty.granicus.com",
        1,
    )
    seen = {}

    def fake_run(video_id, out, **kwargs):
        seen.update(kwargs, video_id=video_id)
        return {}

    monkeypatch.setattr(meeting_cli, "run", fake_run)
    assert (
        meeting_cli.main(["run", "123", "--city", str(tmp_path), "--out", str(tmp_path / "o")]) == 0
    )
    assert seen["granicus_host"] == "sanmateocounty.granicus.com"
    assert seen["granicus_view_id"] == 1
    assert seen["roster_path"] == tmp_path / "roster.json"


def test_a_city_folder_without_settings_says_what_to_run(tmp_path, capsys):
    assert meeting_cli.main(["run", "x", "--city", str(tmp_path), "--out", str(tmp_path)]) == 1
    assert "civiccast_brief setup" in capsys.readouterr().err


def test_a_run_without_a_roster_is_refused(tmp_path, capsys):
    assert meeting_cli.main(["run", "x", "--out", str(tmp_path)]) == 1
    assert "roster" in capsys.readouterr().err


def test_setup_command_line(tmp_path, capsys):
    argv = ["setup", str(tmp_path / "c"), "--slug", "ex", "--name", "Ex", "--state", "Ohio"]
    argv += ["--contact-email", "a@example.org", "--podcast-email", "b@example.org"]
    assert top.main(argv) == 0
    assert "roster.json" in capsys.readouterr().out
    assert top.main(argv) == 1
