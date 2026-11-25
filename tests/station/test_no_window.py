"""Steps and deploys started from the windowless desk keep their console hidden."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW exists only on Windows")
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


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW exists only on Windows")
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


class FakeRunWrites(FakeRun):
    """A fake run that also writes the file the caller expects its child to write."""

    def __init__(self, calls: list[dict[str, object]], stdout: bytes = b"") -> None:
        super().__init__(calls)
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        self.calls.append(dict(kwargs))
        Path(argv[-1]).write_text('{"turns": []}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, self.stdout, b"")


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW exists only on Windows")
def test_direct_run_hides_its_console_on_windows(monkeypatch) -> None:
    from civiccast_brief.meeting import direct

    _hidable(monkeypatch, "win32")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(direct.subprocess, "run", FakeRun(calls))

    direct._run(["speech", "x"], 5)

    assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_direct_run_adds_no_flag_on_other_systems(monkeypatch) -> None:
    from civiccast_brief.meeting import direct

    _hidable(monkeypatch, "linux")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(direct.subprocess, "run", FakeRun(calls))

    direct._run(["speech", "x"], 5)

    assert "creationflags" not in calls[0]


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW exists only on Windows")
def test_granicus_audio_hides_its_console_on_windows(monkeypatch, tmp_path: Path) -> None:
    from civiccast_brief.meeting import granicus

    _hidable(monkeypatch, "win32")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(granicus.subprocess, "run", FakeRunWrites(calls))
    cap = granicus.GranicusCapture(
        clip=granicus.Clip(
            host="h.granicus.com",
            view_id=2,
            clip_id=1,
            name="Council",
            date="November 4, 2026",
            mp3_url="https://h.granicus.com/a.mp3",
        ),
        caption_file=tmp_path / "1.captions.vtt",
        caption_reused=False,
        items=[],
    )

    granicus.fetch_audio(cap, tmp_path)

    assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW exists only on Windows")
def test_diarize_hides_its_console_on_windows(monkeypatch, tmp_path: Path) -> None:
    from civiccast_brief.meeting import diarize

    _hidable(monkeypatch, "win32")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(diarize.subprocess, "run", FakeRunWrites(calls, stdout=b'{"turns": []}'))

    diarize.run_diarization(tmp_path / "a.wav", tmp_path / "out.json")

    assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_diarize_adds_no_flag_on_other_systems(monkeypatch, tmp_path: Path) -> None:
    from civiccast_brief.meeting import diarize

    _hidable(monkeypatch, "linux")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(diarize.subprocess, "run", FakeRunWrites(calls, stdout=b'{"turns": []}'))

    diarize.run_diarization(tmp_path / "a.wav", tmp_path / "out.json")

    assert "creationflags" not in calls[0]
