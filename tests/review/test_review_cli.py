"""The review command line: export summary, bad inputs, serve wiring, and the module entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from civiccast_brief.review import cli
from civiccast_brief.review.cli import build_parser, main
from civiccast_brief.review.store import ReviewStore

SPEAKERS = {"SPEAKER_00": "Mayor Smith"}


def seed(db: Path) -> None:
    store = ReviewStore(db)
    approved = store.add_item(
        meeting_id="approved-meeting",
        title="Approved",
        meeting_date="2026-11-04",
        speakers=SPEAKERS,
        recap="Recap.",
        script="[S1] Script.",
    )
    store.approve(approved.id)
    store.add_item(
        meeting_id="draft-meeting",
        title="Draft",
        meeting_date="2026-11-04",
        speakers=SPEAKERS,
        recap="Recap.",
        script="[S1] Script.",
    )


class FakeServer:
    server_address = ("127.0.0.1", 8765)

    def __init__(self) -> None:
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def test_serve_takes_no_host_option() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--db", "x.db", "--host", "0.0.0.0"])


def test_port_must_be_a_number_in_range() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--db", "x.db", "--port", "70000"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--db", "x.db", "--port", "eighty"])


def test_serve_prints_the_loopback_address_and_closes_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer()
    monkeypatch.setattr(cli, "make_review_server", lambda store, port: server)

    code = main(["serve", "--db", str(tmp_path / "review.db"), "--port", "0"])

    assert code == 0
    assert "http://127.0.0.1:8765/" in capsys.readouterr().out
    assert server.closed is True
    assert (tmp_path / "review.db").is_file()


def test_serve_reports_an_unopenable_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["serve", "--db", str(tmp_path / "no-such-folder" / "review.db")])

    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_export_reports_each_item_and_the_totals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "review.db"
    seed(db)

    code = main(["export", "--db", str(db), "--out", str(tmp_path / "export")])

    out = capsys.readouterr().out
    assert code == 0
    assert "exported:" in out and "approved-meeting" in out
    assert "skipped item" in out and "draft-meeting" in out
    assert "1 exported, 1 skipped" in out


def test_export_refuses_a_missing_database_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "missing.db"

    code = main(["export", "--db", str(db), "--out", str(tmp_path / "export")])

    assert code == 1
    assert "database not found" in capsys.readouterr().err
    assert not db.exists()
    assert not (tmp_path / "export").exists()


def test_export_reports_a_file_that_is_not_a_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "review.db"
    db.write_bytes(b"this is not a sqlite database" * 10)

    code = main(["export", "--db", str(db), "--out", str(tmp_path / "export")])

    assert code == 1
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "export").exists()


def test_module_entry_point_lists_both_commands() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "civiccast_brief.review", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert proc.returncode == 0
    assert "serve" in proc.stdout and "export" in proc.stdout


def test_module_entry_point_exports_only_approved_items(tmp_path: Path) -> None:
    db = tmp_path / "review.db"
    seed(db)
    out = tmp_path / "export"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "civiccast_brief.review",
            "export",
            "--db",
            str(db),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    folders = sorted(p.name for p in out.iterdir() if p.is_dir())
    assert folders == ["0001-approved-meeting"]
