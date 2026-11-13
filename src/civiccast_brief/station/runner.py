"""The weekly job on a schedule, with a record of every run.

The job is a list of commands (find and run the week's meetings, then publish). The station
runs them in order and stops at the first that fails. Every run is a row in ``station_runs``
in the desk's own database, with its output, so the desk can show the last run, the next run
and each run's log.

Only one run goes at a time: a run starts only when no row says ``running``, checked and
written in one ``BEGIN IMMEDIATE`` transaction, so Run now and the schedule cannot overlap.
One station uses one database; a station that stops in the middle of a run marks that run
``interrupted`` when it starts again.

A slot missed while the station was off (the PC was asleep on Monday morning) runs once when
the station comes back; a new install waits for its first slot.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import sys
import threading
import time as _time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

#: How much of a run's output is kept: the end, where the failure is.
MAX_LOG_CHARS: Final = 200_000
#: How often a running job's output is written to the database for the desk to show.
LOG_FLUSH_SECONDS: Final = 2.0
#: The longest the scheduler sleeps before it looks at the clock again.
MAX_WAIT_SECONDS: Final = 60.0
WEEKDAYS: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
RUN_STATES: Final = ("running", "ok", "failed", "interrupted")

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS station_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    exit_code INTEGER,
    log TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS station_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StationError(ValueError):
    """The station's settings are wrong."""


@dataclass(frozen=True)
class Schedule:
    """Once a week, on ``weekday`` (0 is Monday) at ``at``, in ``zone``."""

    weekday: int
    at: time
    zone: ZoneInfo

    @classmethod
    def parse(cls, weekday: str, at: str, zone: str) -> Schedule:
        day = weekday.strip().lower()
        if day not in WEEKDAYS:
            raise StationError(f"the schedule's day must be one of {', '.join(WEEKDAYS)}")
        try:
            clock = time.fromisoformat(at.strip())
        except ValueError:
            raise StationError("the schedule's time must look like 06:00") from None
        try:
            tz = ZoneInfo(zone.strip())
        except (KeyError, ValueError):
            raise StationError(f"{zone!r} is not a time zone, such as America/Denver") from None
        return cls(WEEKDAYS.index(day), clock.replace(tzinfo=None), tz)

    def last_at_or_before(self, moment: datetime) -> datetime:
        local = moment.astimezone(self.zone)
        day = local.date() - timedelta(days=(local.weekday() - self.weekday) % 7)
        slot = datetime.combine(day, self.at, self.zone)
        return slot if slot <= local else slot - timedelta(days=7)

    def next_after(self, moment: datetime) -> datetime:
        return self.last_at_or_before(moment) + timedelta(days=7)

    def describe(self) -> str:
        return f"{WEEKDAYS[self.weekday].capitalize()}s at {self.at:%H:%M} ({self.zone.key})"


@dataclass(frozen=True)
class Run:
    id: int
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    exit_code: int | None
    log: str

    @property
    def seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class StationStatus:
    schedule: str
    next_at: datetime
    running: Run | None
    last: Run | None
    runs: list[Run]


#: Runs one command; calls ``output`` with each line; returns the exit code.
CommandRunner = Callable[[Sequence[str], Path, Callable[[str], None]], int]


def run_command(argv: Sequence[str], cwd: Path, output: Callable[[str], None]) -> int:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    try:
        with subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                output(line.rstrip("\n"))
            return proc.wait()
    except OSError as exc:
        output(f"could not start {argv[0]}: {exc}")
        return 127


def _now() -> datetime:
    return datetime.now().astimezone()


class Station:
    """Runs ``steps`` on ``schedule`` and when asked, one run at a time."""

    def __init__(
        self,
        db: str | Path,
        steps: Sequence[Sequence[str]],
        schedule: Schedule,
        *,
        cwd: str | Path = ".",
        clock: Callable[[], datetime] = _now,
        runner: CommandRunner = run_command,
    ) -> None:
        if not steps:
            raise StationError("the job has no commands")
        self.db = Path(db)
        self.steps = [
            [sys.executable if part == "{python}" else str(part) for part in step] for step in steps
        ]
        self.schedule = schedule
        self.cwd = Path(cwd)
        self.clock = clock
        self.runner = runner
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO station_meta (key, value) VALUES ('since', ?)",
                (self.clock().isoformat(),),
            )
            conn.execute(
                "UPDATE station_runs SET status = 'interrupted', finished_at = ?"
                " WHERE status = 'running'",
                (self.clock().isoformat(),),
            )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            yield conn
        finally:
            conn.close()

    # ----- what the desk shows -----------------------------------------------------------------

    def runs(self, limit: int = 20) -> list[Run]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, trigger, started_at, finished_at, status, exit_code, log"
                " FROM station_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_run(row, self.schedule.zone) for row in rows]

    def run(self, run_id: int) -> Run | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, trigger, started_at, finished_at, status, exit_code, log"
                " FROM station_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return _run(row, self.schedule.zone) if row else None

    def status(self) -> StationStatus:
        runs = self.runs()
        running = next((r for r in runs if r.status == "running"), None)
        last = next((r for r in runs if r.status != "running"), None)
        return StationStatus(
            schedule=self.schedule.describe(),
            next_at=self.schedule.next_after(self.clock()),
            running=running,
            last=last,
            runs=runs,
        )

    # ----- running -----------------------------------------------------------------------------

    def _begin(self, trigger: str) -> int | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("SELECT 1 FROM station_runs WHERE status = 'running'").fetchone():
                    conn.execute("ROLLBACK")
                    return None
                cur = conn.execute(
                    "INSERT INTO station_runs (trigger, started_at, status)"
                    " VALUES (?, ?, 'running')",
                    (trigger, self.clock().isoformat()),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return int(cur.lastrowid or 0)

    def _write_log(self, run_id: int, lines: list[str]) -> None:
        text = "\n".join(lines)[-MAX_LOG_CHARS:]
        with self._connect() as conn:
            conn.execute("UPDATE station_runs SET log = ? WHERE id = ?", (text, run_id))

    def _execute(self, run_id: int) -> None:
        lines: list[str] = []
        flushed = _time.monotonic()

        def output(line: str) -> None:
            nonlocal flushed
            lines.append(line)
            if _time.monotonic() - flushed >= LOG_FLUSH_SECONDS:
                self._write_log(run_id, lines)
                flushed = _time.monotonic()

        code = 0
        try:
            for step in self.steps:
                output("> " + " ".join(step))
                code = self.runner(step, self.cwd, output)
                if code != 0:
                    output(f"stopped: that step ended with code {code}")
                    break
        except Exception as exc:  # noqa: BLE001 - a run must end with a record, whatever broke
            output(f"stopped: {type(exc).__name__}: {exc}")
            code = code or 1
        with self._connect() as conn:
            conn.execute(
                "UPDATE station_runs SET status = ?, exit_code = ?, finished_at = ?, log = ?"
                " WHERE id = ?",
                (
                    "ok" if code == 0 else "failed",
                    code,
                    self.clock().isoformat(),
                    "\n".join(lines)[-MAX_LOG_CHARS:],
                    run_id,
                ),
            )

    def run_once(self, trigger: str) -> int | None:
        """Run the job now, in this thread. None when a run is already going."""
        run_id = self._begin(trigger)
        if run_id is not None:
            self._execute(run_id)
        return run_id

    def run_now(self) -> int | None:
        """Start the job in the background (the desk's Run now). None when one is going."""
        run_id = self._begin("manual")
        if run_id is None:
            return None
        worker = threading.Thread(
            target=self._execute, args=(run_id,), name=f"station-run-{run_id}", daemon=True
        )
        self._workers.append(worker)
        worker.start()
        return run_id

    def due(self) -> bool:
        """A slot has passed since the last scheduled run (or since the station was set up)."""
        slot = self.schedule.last_at_or_before(self.clock())
        with self._connect() as conn:
            since = conn.execute("SELECT value FROM station_meta WHERE key = 'since'").fetchone()
            last = conn.execute(
                "SELECT MAX(started_at) FROM station_runs WHERE trigger = 'schedule'"
            ).fetchone()
        marks = [datetime.fromisoformat(v) for v in (since[0], last[0]) if v]
        return slot > max(marks)

    def tick(self) -> int | None:
        """Start the scheduled run if it is due. The scheduler calls this; so can a test."""
        return self.run_once("schedule") if self.due() else None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the schedule alive; the next look may work
                print(f"station: the scheduled run could not start: {exc}", file=sys.stderr)
            wait = (self.schedule.next_after(self.clock()) - self.clock()).total_seconds()
            self._wake.wait(max(1.0, min(wait, MAX_WAIT_SECONDS)))
            self._wake.clear()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="station", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def join_workers(self, timeout: float = 30.0) -> None:
        """Wait for Run now runs to finish (tests use it)."""
        for worker in self._workers:
            worker.join(timeout)
        self._workers = [w for w in self._workers if w.is_alive()]


def _run(row: Sequence[object], zone: ZoneInfo) -> Run:
    """A run's row, its times shown in the schedule's zone (MST, not UTC-07:00)."""
    rid, trigger, started, finished, status, code, log = row
    return Run(
        id=int(str(rid)),
        trigger=str(trigger),
        started_at=datetime.fromisoformat(str(started)).astimezone(zone),
        finished_at=datetime.fromisoformat(str(finished)).astimezone(zone) if finished else None,
        status=str(status),
        exit_code=int(str(code)) if code is not None else None,
        log=str(log or ""),
    )
