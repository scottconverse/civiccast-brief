"""Steps and deploys started from the windowless desk keep their console hidden."""

from __future__ import annotations

import subprocess
from pathlib import Path

from civiccast_brief.speech import windows_process
from civiccast_brief.station import deploy, runner


def _hidable(monkeypatch, platform: str) -> None:
    """Make the helper believe a windowless pythonw parent is running."""
    monkeypatch.setattr(windows_process.sys, "platform", platform)
    monkeypatch.setattr(windows_process.sys, "executable", r"C:\Python\pythonw.exe")


class FakePopen:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self.calls = calls

    def __call__(self, argv, **kwargs):
        self.calls.append(dict(kwargs))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def stdout(self):
        return iter(["step done\n"])

    def wait(self) -> int:
        return 0


def test_a_station_step_hides_its_console_on_windows(monkeypatch, tmp_path: Path) -> None:
    _hidable(monkeypatch, "win32")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen(calls))
    lines: list[str] = []

    code = runner.run_command(["python", "-m", "x"], tmp_path, lines.append)

    assert code == 0
    assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert lines == ["step done"]


def test_a_station_step_adds_no_flag_on_other_systems(monkeypatch, tmp_path: Path) -> None:
    _hidable(monkeypatch, "linux")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen(calls))

    runner.run_command(["python", "-m", "x"], tmp_path, lambda line: None)

    assert "creationflags" not in calls[0]


class FakeRun:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self.calls = calls

    def __call__(self, argv, **kwargs):
        self.calls.append(dict(kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_a_deploy_hides_its_console_on_windows(monkeypatch) -> None:
    _hidable(monkeypatch, "win32")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(deploy.subprocess, "run", FakeRun(calls))

    assert deploy.run_command(["npx", "wrangler", "--version"], {}) == (0, "")

    assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_a_deploy_adds_no_flag_on_other_systems(monkeypatch) -> None:
    _hidable(monkeypatch, "linux")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(deploy.subprocess, "run", FakeRun(calls))

    deploy.run_command(["npx", "wrangler", "--version"], {})

    assert "creationflags" not in calls[0]
