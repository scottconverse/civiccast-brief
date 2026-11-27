"""One Claude run at a time on this PC: the lock file, the wait, and the two callers."""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from pathlib import Path

import pytest

from civiccast_brief import models as M
from civiccast_brief.article import skill
from civiccast_brief.claude_lock import WAITING_LINE, claude_turn


def _lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIVICCAST_CLAUDE_LOCK", str(tmp_path / "x.lock"))


def test_a_free_lock_is_taken_and_freed(tmp_path, monkeypatch):
    _lock(tmp_path, monkeypatch)
    said: list[str] = []
    with claude_turn(say=said.append, poll_s=0.01):
        assert (tmp_path / "x.lock").is_file()
    with claude_turn(say=said.append, poll_s=0.01):
        pass
    assert said == []


def test_a_second_thread_waits_until_the_first_leaves(tmp_path, monkeypatch):
    _lock(tmp_path, monkeypatch)
    said: list[str] = []
    order: list[tuple[str, str]] = []

    def worker(name: str, hold_s: float) -> None:
        with claude_turn(say=said.append, poll_s=0.01):
            order.append((name, "enter"))
            time.sleep(hold_s)
            order.append((name, "leave"))

    first = threading.Thread(target=worker, args=("first", 0.1))
    first.start()
    while ("first", "enter") not in order:
        time.sleep(0.001)
    second = threading.Thread(target=worker, args=("second", 0.0))
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert order == [
        ("first", "enter"),
        ("first", "leave"),
        ("second", "enter"),
        ("second", "leave"),
    ]
    assert said.count(WAITING_LINE) == 1
    assert sum(1 for line in said if line.startswith("claude: waited ")) == 1


def test_the_lock_is_freed_when_the_body_raises(tmp_path, monkeypatch):
    _lock(tmp_path, monkeypatch)
    said: list[str] = []
    with pytest.raises(RuntimeError, match="boom"), claude_turn(say=said.append, poll_s=0.01):
        raise RuntimeError("boom")
    with claude_turn(say=said.append, poll_s=0.01):
        pass
    assert said == []


@contextlib.contextmanager
def _recording_turn(events: list[str]):
    events.append("enter")
    try:
        yield
    finally:
        events.append("leave")


def test_run_process_runs_claude_inside_a_turn(tmp_path, monkeypatch):
    events: list[str] = []

    def fake_run(cmd, **kwargs):
        events.append("run")
        return subprocess.CompletedProcess(cmd, 0, "out", "err")

    monkeypatch.setattr(M, "claude_turn", lambda: _recording_turn(events))
    monkeypatch.setattr(M.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(M.subprocess, "run", fake_run)

    assert M.run_process(["claude", "-p"], "PROMPT", {}) == (0, "out", "err")
    assert events == ["enter", "run", "leave"]


def test_claude_agent_runs_inside_a_turn(tmp_path, monkeypatch):
    events: list[str] = []

    def fake_run(cmd, **kwargs):
        events.append("run")
        return subprocess.CompletedProcess(cmd, 0, "out", "err")

    monkeypatch.setattr(skill, "claude_turn", lambda: _recording_turn(events))
    monkeypatch.setattr(skill.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(skill.subprocess, "run", fake_run)

    code, out, err = skill.claude_agent("PROMPT", tmp_path, "claude-opus-5-5", 60)
    assert (code, out, err) == (0, "out", "err")
    assert events == ["enter", "run", "leave"]
