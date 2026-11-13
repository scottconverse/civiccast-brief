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
YOUTUBE = {**ARGS, "platform": "youtube"}


def test_setup_writes_a_city_the_site_build_reads(tmp_path):
    folder = tmp_path / "smc"
    written = setup(folder, roster_page="https://example.org/board", **YOUTUBE)
    assert [p.name for p in written] == ["city.json", "roster.json"]
    site = load_site_input(folder)  # roster.json is not read as an episode
    assert site.city.slug == "san-mateo-county" and site.episodes == ()
    roster = json.loads((folder / "roster.json").read_text(encoding="utf-8"))
    assert roster["members"] == [] and roster["sources"] == ["https://example.org/board"]
    with pytest.raises(ValueError, match="roster has no members"):
        load_roster(folder / "roster.json")


def test_setup_never_overwrites(tmp_path):
    setup(tmp_path, **YOUTUBE)
    with pytest.raises(CityError, match="not overwritten"):
        setup(tmp_path, **YOUTUBE)


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
    argv += ["--platform", "youtube"]
    assert top.main(argv) == 0
    assert "roster.json" in capsys.readouterr().out
    assert top.main(argv) == 1


def _bare_argv(folder, *extra, state="Ohio"):
    """A setup command line that names no video platform unless ``extra`` does."""
    argv = ["setup", str(folder), "--slug", "ex", "--name", "Ex", "--state", state]
    return argv + ["--contact-email", "a@example.org", "--podcast-email", "b@example.org", *extra]


def _setup_argv(folder, *extra, state="Ohio"):
    return _bare_argv(folder, *(extra or ("--platform", "youtube")), state=state)


def test_setup_next_steps_follow_the_docs_order(tmp_path, capsys):
    assert top.main(_setup_argv(tmp_path / "c")) == 0
    out = capsys.readouterr().out
    sources, meeting, roster = (
        out.index("add the city's sources"),
        out.index("run one meeting"),
        out.index("Roster page"),
    )
    assert sources < meeting < roster
    # Members come from the meeting's records during the run, never typed in by hand.
    assert "add the members" not in out
    assert "from that meeting's own records" in out
    assert "city find" in out and "city check" in out and "Sources screen" in out


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("CO", "Colorado"),
        ("co", "Colorado"),
        ("NH", "New Hampshire"),
        ("WV", "West Virginia"),
        ("DC", "District of Columbia"),
        ("Colorado", "Colorado"),
    ],
)
def test_setup_takes_a_state_code_and_stores_the_full_name(tmp_path, given, stored):
    assert top.main(_setup_argv(tmp_path / "c", state=given)) == 0
    roster = json.loads((tmp_path / "c" / "roster.json").read_text(encoding="utf-8"))
    assert roster["state"] == stored


def test_setup_says_which_capture_platform_it_set(tmp_path, capsys):
    assert top.main(_setup_argv(tmp_path / "y", "--platform", "youtube")) == 0
    out = capsys.readouterr().out
    assert "capture.platform" in out and "'youtube'" in out and "meeting run" in out
    granicus = ["--granicus", "ex.granicus.com", "--view-id", "2"]
    assert top.main(_setup_argv(tmp_path / "g", *granicus)) == 0
    assert "'granicus'" in capsys.readouterr().out


def test_setup_never_picks_youtube_for_a_city_that_named_no_platform(tmp_path, capsys):
    # No flag, nothing found, and no terminal to ask on (pytest's stdin is not one).
    assert top.main(_bare_argv(tmp_path / "c")) == 2
    err = capsys.readouterr().err
    assert "no video platform was found" in err and "--platform" in err
    assert not (tmp_path / "c" / "city.json").exists()
    with pytest.raises(CityError, match="say which video platform"):
        setup(tmp_path / "d", **ARGS)


def test_setup_takes_the_one_platform_it_finds(tmp_path, capsys):
    assert top.main(_bare_argv(tmp_path / "c", "--cablecast", "reflect-ex.cablecast.tv")) == 0
    out = capsys.readouterr().out
    assert "Video platform: Cablecast" in out and "'cablecast'" in out
    capture = json.loads((tmp_path / "c" / "city.json").read_text(encoding="utf-8"))["capture"]
    assert capture == {"platform": "cablecast", "host": "reflect-ex.cablecast.tv"}
    assert load_city(tmp_path / "c").cablecast_host == "reflect-ex.cablecast.tv"


def test_setup_reads_the_platform_from_the_folders_sources(tmp_path, capsys):
    from civiccast_brief.sources import add_source, make_source

    folder = tmp_path / "c"
    folder.mkdir()
    add_source(
        folder,
        make_source(
            "granicus", {"host": "ex.granicus.com", "view_id": "7", "bodies": ["City Council"]}
        ),
    )
    assert top.main(_bare_argv(folder)) == 0
    capture = json.loads((folder / "city.json").read_text(encoding="utf-8"))["capture"]
    assert capture == {"platform": "granicus", "host": "ex.granicus.com", "view_id": 7}


def test_setup_asks_when_two_platforms_fit_and_stops_when_it_cannot_ask(
    tmp_path, capsys, monkeypatch
):
    both = [
        "--granicus",
        "ex.granicus.com",
        "--view-id",
        "2",
        "--cablecast",
        "reflect-ex.cablecast.tv",
    ]
    assert top.main(_bare_argv(tmp_path / "n", *both)) == 2
    assert "more than one video platform was found: granicus, cablecast" in capsys.readouterr().err
    monkeypatch.setattr(top.sys.stdin, "isatty", lambda: True)
    answers = iter(["9", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert top.main(_bare_argv(tmp_path / "a", *both)) == 0
    out = capsys.readouterr().out
    assert "1. Granicus  (found: --granicus)" in out and "2. Cablecast" in out
    capture = json.loads((tmp_path / "a" / "city.json").read_text(encoding="utf-8"))["capture"]
    assert capture["platform"] == "cablecast"


def test_a_cablecast_city_reaches_the_meeting_run(tmp_path, monkeypatch):
    setup(
        tmp_path,
        cablecast_host="reflect-ex.cablecast.tv",
        **ARGS,
    )
    seen = {}

    def fake_run(video_id, out, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(meeting_cli, "run", fake_run)
    argv = ["run", "5", "--city", str(tmp_path), "--out", str(tmp_path / "o")]
    assert meeting_cli.main(argv) == 0
    assert seen["cablecast_host"] == "reflect-ex.cablecast.tv"
