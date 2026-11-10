"""The desk command line: export summary, bad inputs, serve wiring, and the module entry point."""

from __future__ import annotations

import subprocess
import sys

import pytest

from civiccast_brief.review import cli
from civiccast_brief.review.cli import build_parser, main
from civiccast_brief.review.store import ReviewStore


def seed(db) -> None:
    with ReviewStore(db) as store:
        for meeting_id in ("published-meeting", "review-meeting"):
            store.add_item(
                meeting_id=meeting_id,
                title="T",
                meeting_date="2026-11-04",
                speakers={"voice 01": "Mayor"},
                recap="Recap.",
                script="[S1] Script.",
            )
        store.publish(store.list_items()[-1].id)


def test_serve_takes_no_host_option_and_checks_the_port() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--db", "x.db", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--db", "x.db", "--port", "70000"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--db", "x.db", "--port", "web"])


def test_export_prints_what_it_wrote_and_skipped(tmp_path, capsys) -> None:
    db = tmp_path / "d.db"
    seed(db)
    assert main(["export", "--db", str(db), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "1 exported, 1 skipped" in out
    assert "status is review" in out


def test_export_of_a_missing_database_fails_cleanly(tmp_path, capsys) -> None:
    assert main(["export", "--db", str(tmp_path / "no.db"), "--out", str(tmp_path / "o")]) == 1
    assert "database not found" in capsys.readouterr().err


def test_serve_wires_the_city_folder_and_refuses_a_missing_one(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_serve(app, port):
        seen["port"] = port
        seen["city"] = app.state.desk.city_dir

    monkeypatch.setattr(cli, "serve", fake_serve)
    city = tmp_path / "city"
    city.mkdir()
    args = ["serve", "--db", str(tmp_path / "d.db"), "--city", str(city), "--port", "9001"]
    assert main(args) == 0
    assert seen == {"port": 9001, "city": city}
    assert "http://127.0.0.1:9001/" in capsys.readouterr().out
    missing = ["serve", "--db", str(tmp_path / "d.db"), "--city", str(tmp_path / "none")]
    assert main(missing) == 1
    assert "does not exist" in capsys.readouterr().err


def test_module_entry_point_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "civiccast_brief.review", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "Editor desk" in result.stdout
