"""The one Windows scheduled task that runs every city's weekly job.

The desk's own scheduler (``station/runner.py``) only runs while the desk is open. This task
runs ``python -m civiccast_brief.review run-due --cities DIR`` every hour and at sign-in, with
"start when available", so a week that passed while the PC was off is caught up as soon as the
task next runs. Each city's Station refuses a second run while one is going, so the task and an
open desk never run the same city twice.

The task is registered for the current user, in the user's own task folder, so it needs no
administrator rights. No password is stored: the task runs only while this user is signed in to
Windows. After a reboot it runs at the next sign-in, and its missed week runs then.

Every PowerShell call goes through an injectable ``runner``: it takes the full argv and returns
``(returncode, stdout, stderr)``. Tests pass a fake and check the exact argv, so no test ever
touches a real scheduled task.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from civiccast_brief.speech.windows_process import hidden_process_kwargs

#: The task's name in Task Scheduler.
TASK_NAME: Final = "CivicCast Brief weekly"
#: The PowerShell executable the module runs. ``powershell.exe`` is on PATH on Windows.
POWERSHELL: Final = "powershell.exe"
#: Task Scheduler's result code before a task has ever run.
NEVER_RUN: Final = 267011

#: Runs PowerShell: takes the argv, returns (return code, stdout, stderr).
TaskRunner = Callable[[Sequence[str]], tuple[int, str, str]]


class WeeklyTaskError(RuntimeError):
    """PowerShell could not register, read or remove the task; the message says why."""


def run_powershell(argv: Sequence[str]) -> tuple[int, str, str]:
    """The default runner: PowerShell with no profile and no prompts."""
    proc = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_process_kwargs(),
    )
    return proc.returncode, proc.stdout, proc.stderr


@dataclass(frozen=True)
class TaskStatus:
    """What Task Scheduler says about the task."""

    installed: bool
    state: str = ""
    next_run: datetime | None = None
    last_run: datetime | None = None
    last_result: int | None = None

    @property
    def last_result_text(self) -> str:
        """The last result in plain words."""
        if not self.installed or self.last_result is None:
            return "not run yet"
        if self.last_result == NEVER_RUN:
            return "not run yet"
        if self.last_result == 0:
            return "ok"
        if self.last_result == 267009:
            return "running"
        return f"code {self.last_result}"


def _ps(value: object) -> str:
    """A PowerShell single-quoted string; a single quote inside is doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def _command(argument: str) -> str:
    """The command line the task runs: the module, then the cities folder in double quotes.

    Windows paths cannot hold a double quote, so quoting the path here is safe; the whole
    argument string is later wrapped in a PowerShell single-quoted literal."""
    return f'-m civiccast_brief.review run-due --cities "{argument}"'


def quiet_python(python: str | Path) -> str:
    """``pythonw.exe`` beside the given python when it exists, so no console flashes hourly.

    The task runs only to write each city's ``desk.db``; with no console window nobody sees a
    flash every hour. Any other python (no ``pythonw.exe`` beside it) is used as given."""
    path = Path(python)
    if path.name.lower() == "python.exe":
        quiet = path.with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return str(python)


def install_script(python: str | Path, cities_dir: str | Path) -> str:
    """The PowerShell that registers the task for the current user, with no password.

    The trigger names this user and the principal is interactive with limited rights, so a
    non-elevated user may register it. The hourly trigger carries no ``-RepetitionDuration``:
    Task Scheduler then repeats with no end, which it accepts as ``PT1H``."""
    action = f"New-ScheduledTaskAction -Execute {_ps(python)} -Argument {_ps(_command(cities_dir))}"
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            '$me = "$env:USERDOMAIN\\$env:USERNAME"',
            f"$action = {action}",
            "$logon = New-ScheduledTaskTrigger -AtLogOn -User $me",
            (
                "$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date)"
                " -RepetitionInterval (New-TimeSpan -Hours 1)"
            ),
            (
                "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable"
                " -MultipleInstances IgnoreNew"
            ),
            (
                "$principal = New-ScheduledTaskPrincipal -UserId $me"
                " -LogonType Interactive -RunLevel Limited"
            ),
            (
                f"Register-ScheduledTask -TaskName {_ps(TASK_NAME)} -Action $action"
                " -Trigger @($logon, $hourly) -Settings $settings -Principal $principal"
                " -Force | Out-Null"
            ),
        ]
    )


def status_script() -> str:
    """The PowerShell that reads the task's state and times as one JSON line."""
    name = _ps(TASK_NAME)
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$task = Get-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue",
            "if ($null -eq $task) { '{\"installed\": false}'; exit 0 }",
            f"$info = Get-ScheduledTaskInfo -TaskName {name}",
            "$next = ''",
            "if ($info.NextRunTime) { $next = $info.NextRunTime.ToString('o') }",
            "$last = ''",
            "if ($info.LastRunTime) { $last = $info.LastRunTime.ToString('o') }",
            (
                "[pscustomobject]@{ installed = $true; state = [string]$task.State;"
                " next = $next; last = $last; result = [int]$info.LastTaskResult }"
                " | ConvertTo-Json -Compress"
            ),
        ]
    )


def remove_script() -> str:
    """The PowerShell that removes the task, or says it was not there."""
    name = _ps(TASK_NAME)
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$task = Get-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue",
            (
                "if ($null -eq $task) { 'not-installed' } else {"
                f" Unregister-ScheduledTask -TaskName {name} -Confirm:$false; 'removed' }}"
            ),
        ]
    )


def _argv(script: str) -> list[str]:
    return [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script]


def _plain(text: str, code: int) -> str:
    line = " ".join(text.split())
    return f"PowerShell could not set up the task (code {code}): {line[:300]}"


def _call(runner: TaskRunner, script: str) -> str:
    code, out, err = runner(_argv(script))
    if code != 0:
        raise WeeklyTaskError(_plain(err or out, code))
    return out


def install(
    python: str | Path, cities_dir: str | Path, runner: TaskRunner = run_powershell
) -> None:
    """Register the hourly task for the current user, replacing one with the same name.

    ``pythonw.exe`` beside ``python`` runs the task when it exists, so no console window
    flashes every hour."""
    _call(runner, install_script(quiet_python(python), cities_dir))


def status(runner: TaskRunner = run_powershell) -> TaskStatus:
    """Whether the task is installed, and its next run, last run and last result."""
    text = _call(runner, status_script()).strip()
    try:
        data = json.loads(text.splitlines()[-1] if text else "{}")
    except ValueError as exc:
        raise WeeklyTaskError(f"Task Scheduler's answer was not readable: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise WeeklyTaskError("Task Scheduler's answer was not a JSON object.")
    if not data.get("installed"):
        return TaskStatus(installed=False)
    return TaskStatus(
        installed=True,
        state=str(data.get("state") or ""),
        next_run=_moment(data.get("next")),
        last_run=_moment(data.get("last")),
        last_result=int(data["result"]) if data.get("result") is not None else None,
    )


def remove(runner: TaskRunner = run_powershell) -> bool:
    """Remove the task; True when one was removed, False when none was installed."""
    text = _call(runner, remove_script()).strip()
    return text.splitlines()[-1].strip().lower() == "removed" if text else False


def _moment(text: object) -> datetime | None:
    """An ISO time from Task Scheduler, or None for its empty/never values."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # Task Scheduler's "never" values are year 1 or 1899; show those as no time at all.
    return moment if moment.year >= 2000 else None
