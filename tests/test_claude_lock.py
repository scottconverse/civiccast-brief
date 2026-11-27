"""One Claude run at a time on this PC: the lock file, the wait, and the two callers."""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO

import pytest

from civiccast_brief import models as M
from civiccast_brief.article import skill
from civiccast_brief.claude_lock import WAITING_LINE, claude_turn


def _lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIVICCAST_CLAUDE_LOCK", str(tmp_path / "x.lock"))
    # These tests are about the lock itself, so they turn on the turns the weekly run sets.
    monkeypatch.setenv("CIVICCAST_CLAUDE_TURNS", "1")


def _hold(path: Path) -> BinaryIO:
    """Hold the lock the way ``_try_lock`` does, so a call there has to wait."""
    handle = open(path, "a+b")  # noqa: SIM115 - the handle is the lock; closed by _release
    if os.name == "nt":
        import msvcrt

        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def _release(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def test_without_the_turns_variable_a_call_runs_at_once_and_never_waits(tmp_path, monkeypatch):
    _lock(tmp_path, monkeypatch)
    monkeypatch.delenv("CIVICCAST_CLAUDE_TURNS", raising=False)
    holder = _hold(tmp_path / "x.lock")
    try:
        said: list[str] = []
        entered = False
        with claude_turn(say=said.append, poll_s=0.01):
            entered = True
        assert entered
        assert said == []
    finally:
        _release(holder)


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
