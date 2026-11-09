"""The ``tools`` command: install yt-dlp into CivicCast Brief's own folder."""

from __future__ import annotations

import pytest

from civiccast_brief import __main__ as cli
from civiccast_brief import tools
from civiccast_brief.record.runtime import UpdateOutcome


def test_no_test_video_is_a_plain_refusal(monkeypatch):
    monkeypatch.delenv(tools.TEST_VIDEO_ENV, raising=False)
    with pytest.raises(tools.ToolsError, match="name a test video"):
        tools.test_video(None)


def test_the_argument_wins_over_the_setting(monkeypatch):
    monkeypatch.setenv(tools.TEST_VIDEO_ENV, "https://www.youtube.com/watch?v=fromsetting")
    assert tools.test_video(" https://www.youtube.com/watch?v=arg ") == (
        "https://www.youtube.com/watch?v=arg"
    )
    assert tools.test_video(None) == "https://www.youtube.com/watch?v=fromsetting"


class FakeManager:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def check_and_update(self, url, probe, *, client):
        self.calls.append((url, probe, client))
        return self.outcome


def outcome(**changes):
    base = {
        "checked": "2026.10.26",
        "requested": "2026.10.26",
        "active": "2026.10.26",
        "previous": None,
        "switched": True,
        "failed": False,
        "reason": "yt-dlp 2026.10.26 passed the known-video test and is now active.",
    }
    base.update(changes)
    return UpdateOutcome(**base)


def test_install_hands_the_test_video_to_the_runtime_check():
    manager = FakeManager(outcome())
    result = tools.install_ytdlp("https://www.youtube.com/watch?v=abc", manager=manager)
    assert result.active == "2026.10.26"
    url, probe, client = manager.calls[0]
    assert url == "https://www.youtube.com/watch?v=abc"
    assert callable(probe)
    assert client.headers["User-Agent"] == "civiccast-brief"
    assert client.is_closed  # the client it opened is closed again


def test_cli_prints_the_reason_and_the_active_version(monkeypatch, capsys):
    monkeypatch.setattr(tools, "install_ytdlp", lambda url: outcome())
    assert cli.main(["tools", "--test-video", "https://www.youtube.com/watch?v=abc"]) == 0
    out = capsys.readouterr().out
    assert "passed the known-video test" in out
    assert "yt-dlp 2026.10.26 is active" in out


def test_cli_exits_1_when_the_new_version_fails_its_test(monkeypatch, capsys):
    failed = outcome(
        active=None, switched=False, failed=True, reason="yt-dlp 2026.10.26 failed the test."
    )
    monkeypatch.setattr(tools, "install_ytdlp", lambda url: failed)
    assert cli.main(["tools", "--test-video", "https://www.youtube.com/watch?v=abc"]) == 1
    assert "failed the test" in capsys.readouterr().out


def test_cli_without_a_test_video_says_what_to_do(monkeypatch, capsys):
    monkeypatch.delenv(tools.TEST_VIDEO_ENV, raising=False)
    assert cli.main(["tools"]) == 1
    assert "name a test video" in capsys.readouterr().err
