"""The plain desk's weekly job: its schedule and the command it runs.

``weekly.json`` sits in the city folder, next to ``city.json``::

    {
      "enabled": false,
      "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
      "days": 14,
      "diarize": false
    }

The Weekly job page edits this file. The job itself is one ``civiccast_brief.weekly run`` command
that finds the week's meetings and writes them, staged for review, to the desk's database. It has
no site options: building and deploying the site stays with the weekly command's site options.

A missing file gives these defaults, with the schedule off, so a new desk never runs anything
until its owner turns it on.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from civiccast_brief.station.runner import WEEKDAYS, Schedule, StationError

SETTINGS_NAME: Final = "weekly.json"
DEFAULT_DAY: Final = "monday"
DEFAULT_TIME: Final = "06:00"
DEFAULT_ZONE: Final = "America/Denver"
DEFAULT_DAYS: Final = 14
MIN_DAYS: Final = 1
MAX_DAYS: Final = 60


class WeeklyJobError(ValueError):
    """weekly.json is unreadable, or one of its settings is not usable."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class WeeklyJob:
    """The weekly job's settings, as weekly.json keeps them."""

    enabled: bool = False
    day: str = DEFAULT_DAY
    at: str = DEFAULT_TIME
    zone: str = DEFAULT_ZONE
    days: int = DEFAULT_DAYS
    diarize: bool = False

    @property
    def schedule(self) -> Schedule:
        """The schedule, checked; raises WeeklyJobError when a setting is not usable."""
        return validate(self)

    def to_json(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "schedule": {"day": self.day, "time": self.at, "timezone": self.zone},
            "days": self.days,
            "diarize": self.diarize,
        }


DEFAULT: Final = WeeklyJob()


def validate(job: WeeklyJob) -> Schedule:
    """The job's schedule, or WeeklyJobError naming the field that is wrong."""
    if job.day.strip().lower() not in WEEKDAYS:
        raise WeeklyJobError(f"Choose a day of the week: {', '.join(WEEKDAYS)}.", "day")
    try:
        time.fromisoformat(job.at.strip())
    except ValueError:
        raise WeeklyJobError("Write the time as 06:00.", "time") from None
    try:
        ZoneInfo(job.zone.strip())
    except (KeyError, ValueError):
        raise WeeklyJobError(
            f"{job.zone!r} is not a time zone, such as America/Denver.", "timezone"
        ) from None
    if not MIN_DAYS <= job.days <= MAX_DAYS:
        raise WeeklyJobError(f"Days back must be from {MIN_DAYS} to {MAX_DAYS}.", "days")
    try:
        return Schedule.parse(job.day, job.at, job.zone)
    except StationError as exc:
        raise WeeklyJobError(str(exc)) from None


def load(city_dir: str | Path) -> WeeklyJob:
    """weekly.json from the city folder; defaults (with the schedule off) when it is missing."""
    path = Path(city_dir) / SETTINGS_NAME
    if not path.exists():
        return DEFAULT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WeeklyJobError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WeeklyJobError(f"{path} must hold a JSON object")
    when = data.get("schedule")
    if not isinstance(when, dict):
        raise WeeklyJobError('"schedule" must say the day, time and timezone', "day")
    try:
        days = int(data.get("days", DEFAULT_DAYS))
    except (TypeError, ValueError):
        raise WeeklyJobError('"days" must be a whole number', "days") from None
    job = WeeklyJob(
        enabled=bool(data.get("enabled", False)),
        day=str(when.get("day", DEFAULT_DAY)),
        at=str(when.get("time", DEFAULT_TIME)),
        zone=str(when.get("timezone", DEFAULT_ZONE)),
        days=days,
        diarize=bool(data.get("diarize", False)),
    )
    validate(job)
    return job


def save(city_dir: str | Path, job: WeeklyJob) -> Path:
    """Write weekly.json through a temporary file, so a crash never leaves half a file."""
    validate(job)
    path = Path(city_dir) / SETTINGS_NAME
    text = json.dumps(job.to_json(), indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def from_form(form: Mapping[str, str]) -> WeeklyJob:
    """The job the Schedule form asks for; raises WeeklyJobError naming the bad field."""
    days_text = str(form.get("days", "")).strip()
    try:
        days = int(days_text)
    except ValueError:
        raise WeeklyJobError(f"Days back must be from {MIN_DAYS} to {MAX_DAYS}.", "days") from None
    job = WeeklyJob(
        enabled=_checked(form.get("enabled")),
        day=str(form.get("day", "")),
        at=str(form.get("time", "")),
        zone=str(form.get("timezone", "")),
        days=days,
        diarize=_checked(form.get("diarize")),
    )
    validate(job)
    return job


def form_values(job: WeeklyJob) -> dict[str, str]:
    """The Schedule form's starting values for ``job``."""
    return {
        "enabled": "1" if job.enabled else "",
        "day": job.day.strip().lower(),
        "time": job.at,
        "timezone": job.zone,
        "days": str(job.days),
        "diarize": "1" if job.diarize else "",
    }


def _checked(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "on", "true", "yes")


def job_steps(
    job: WeeklyJob, city_dir: str | Path, db: str | Path, work: str | Path
) -> list[list[str]]:
    """The station job: one command that finds the week's meetings and stages them for review."""
    argv = [
        sys.executable,
        "-m",
        "civiccast_brief.weekly",
        "run",
        "--city",
        str(city_dir),
        "--work",
        str(work),
        "--db",
        str(db),
        "--days",
        str(job.days),
    ]
    if job.diarize:
        argv.append("--diarize")
    # The city's voices, when it has them. The job has no site steps today, so this only
    # matters when the job grows them.
    voice_file = Path(city_dir) / "voices.json"
    if voice_file.is_file():
        argv += ["--voices", str(voice_file)]
    return [argv]
