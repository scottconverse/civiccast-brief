import json
from datetime import datetime
from types import SimpleNamespace as NS

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

    def run_week(settings, meetings, last):
        open_during_run.append(not clients[0].is_closed)
        return NS(lines=[], failed=0)

    monkeypatch.setattr(cli, "real_listers", listers)
    monkeypatch.setattr(cli, "run_week", run_week)
    assert cli._run(_args(tmp_path)) == 0
    assert open_during_run == [True]
    assert clients[0].is_closed
