import json
import os
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from civiccast_brief.speech.windows_process import task_parent_pid
from civiccast_brief.weekly import __main__ as cli
from civiccast_brief.weekly.find import Listers


def _city(tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    (city / "city.json").write_text(json.dumps({"slug": "longmont", "name": "Longmont"}))
    (city / "roster.json").write_text(json.dumps({"body": "City Council"}))
    (city / "sources.json").write_text(
        json.dumps(
            {
                "format": 1,
                "sources": [
                    {
                        "kind": "primegov",
                        "url": "https://longmont.primegov.com",
                        "bodies": ["City Council"],
                    }
                ],
            }
        )
    )
    return city


def _args(tmp_path, *extra):
    base = ["run", "--city", str(_city(tmp_path)), "--work", str(tmp_path / "w")]
    base += ["--db", str(tmp_path / "d.db"), "--first", "2026-11-04", "--last", "2026-11-10"]
    return cli.build_parser().parse_args([*base, *extra])


def test_the_window_is_read_and_a_meeting_that_cannot_run_is_listed(tmp_path, capsys):
    seen = []

    def primegov(url, first, last):
        seen.append((first.isoformat(), last.isoformat()))
        return [
            NS(
                title="City Council - CANCELLED",
                start=datetime(2026, 11, 10, 18),
                video_url=None,
                documents=[],
            )
        ]

    code = cli._run(_args(tmp_path), Listers(primegov=primegov))
    out = capsys.readouterr().out
    assert code == 0
    assert seen == [("2026-11-04", "2026-11-10")]
    assert "Longmont: 1 meeting(s) from 2026-11-04 to 2026-11-10" in out
    assert "2026-11-10 City Council - CANCELLED: not run: the meeting was cancelled" in out


def test_site_settings_must_be_given_together(tmp_path, capsys):
    code = cli._run(_args(tmp_path, "--episodes", str(tmp_path / "e")), Listers())
    assert code == 1
    assert "the site steps need all of" in capsys.readouterr().err


def test_a_city_without_sources_is_refused(tmp_path, capsys):
    args = _args(tmp_path)
    (args.city / "sources.json").unlink()
    assert cli._run(args, Listers()) == 1
    assert "has no sources.json" in capsys.readouterr().err


def test_the_portal_client_stays_open_while_the_week_runs(tmp_path, monkeypatch, capsys):
    # The documents step fetches packets during run_week, over the client the listers made.
    clients = []

    def listers(client):
        clients.append(client)
        return Listers(primegov=lambda url, first, last: [])

    open_during_run = []

    def run_week(settings, meetings, last, limit=None, **kwargs):
        open_during_run.append(not clients[0].is_closed)
        return NS(lines=[], failed=0)

    monkeypatch.setattr(cli, "real_listers", listers)
    monkeypatch.setattr(cli, "run_week", run_week)
    assert cli._run(_args(tmp_path)) == 0
    assert open_during_run == [True]
    assert clients[0].is_closed


def test_publish_needs_the_site_settings(tmp_path, capsys):
    args = cli.build_parser().parse_args(
        ["publish", "--city", str(_city(tmp_path)), "--work", str(tmp_path / "w"),
         "--db", str(tmp_path / "d.db")]
    )  # fmt: skip
    assert cli._publish(args) == 1
    assert "publish needs the site settings" in capsys.readouterr().err


def test_the_pc_worker_folder_is_passed_on_and_refused_with_diarize(tmp_path, capsys, monkeypatch):
    seen = []
    report = NS(lines=[], failed=0)
    monkeypatch.setattr(cli, "run_week", lambda s, *a, **k: seen.append(s) or report)
    args = _args(tmp_path, "--worker", str(tmp_path / "pc"), "--diarize")
    assert cli._run(args, Listers(primegov=lambda *a: [])) == 1
    assert "--diarize needs the meeting's audio" in capsys.readouterr().err
    assert seen == []
    (tmp_path / "2").mkdir()
    again = _args(tmp_path / "2", "--worker", str(tmp_path / "pc"))
    cli._run(again, Listers(primegov=lambda *a: []))
    assert seen and seen[0].worker == tmp_path / "pc"


def _budget_passed(tmp_path, monkeypatch, *extra):
    seen = []
    report = NS(lines=[], failed=0)
    monkeypatch.setattr(cli, "run_week", lambda s, *a, **k: seen.append(k) or report)
    code = cli._run(_args(tmp_path, *extra), Listers(primegov=lambda *a: []))
    assert code == 0
    return seen[0]["time_budget_s"]


def test_the_time_budget_defaults_to_six_hours(tmp_path, monkeypatch):
    assert _budget_passed(tmp_path, monkeypatch) == 21600


def test_zero_hours_means_no_budget(tmp_path, monkeypatch):
    assert _budget_passed(tmp_path, monkeypatch, "--time-budget-hours", "0") is None


def test_the_time_budget_is_given_in_seconds(tmp_path, monkeypatch):
    assert _budget_passed(tmp_path, monkeypatch, "--time-budget-hours", "2.5") == 9000


def test_a_negative_time_budget_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        _args(tmp_path, "--time-budget-hours", "-1")


def test_the_task_parent_pid_is_a_process():
    pid = task_parent_pid()
    assert isinstance(pid, int) and pid > 0


@pytest.mark.skipif(sys.platform != "win32", reason="the launcher's process table is Windows")
def test_the_task_parent_pid_is_the_process_that_started_python():
    code = (
        "from civiccast_brief.speech.windows_process import task_parent_pid; "
        "print(task_parent_pid())"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
        check=True,
    )
    assert int(done.stdout.strip()) == os.getpid()
