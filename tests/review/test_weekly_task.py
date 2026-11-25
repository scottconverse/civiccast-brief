"""The Windows scheduled task: exact PowerShell, status parsing, and plain failures."""

from __future__ import annotations

import json

import pytest

from civiccast_brief.review import weekly_task

PREFIX = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]


def test_install_builds_the_exact_powershell_argv_for_the_current_user() -> None:
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0, "", ""

    weekly_task.install(r"C:\Python\python.exe", r"C:\cities", runner)

    script = r"""$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute 'C:\Python\python.exe' -Argument '-m civiccast_brief.review run-due --cities "C:\cities"'
$logon = New-ScheduledTaskTrigger -AtLogOn
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration ([TimeSpan]::MaxValue)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'CivicCast Brief weekly' -Action $action -Trigger @($logon, $hourly) -Settings $settings -Force | Out-Null"""
    assert calls == [[*PREFIX, script]]


def test_a_path_with_a_space_and_a_single_quote_is_escaped_not_injected() -> None:
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0, "", ""

    weekly_task.install("C:\\Program Files\\Py\\python.exe", "C:\\A B\\O'Brien\\cities", runner)
    script = calls[0][-1]

    assert "-Execute 'C:\\Program Files\\Py\\python.exe'" in script
    assert "--cities \"C:\\A B\\O''Brien\\cities\"" in script
    # The doubled quote is inside a PowerShell single-quoted string; no stray quote ends it.
    assert script.count("Register-ScheduledTask") == 1


def test_status_reads_the_state_times_and_result() -> None:
    def runner(argv):
        return (
            0,
            json.dumps(
                {
                    "installed": True,
                    "state": "Ready",
                    "next": "2026-11-30T06:00:00-07:00",
                    "last": "2026-11-23T06:00:05-07:00",
                    "result": 0,
                }
            ),
            "",
        )

    status = weekly_task.status(runner)
    assert status.installed is True
    assert status.state == "Ready"
    assert status.next_run is not None and status.next_run.hour == 6
    assert status.last_run is not None
    assert status.last_result_text == "ok"


def test_status_says_not_installed_and_never_run_plainly() -> None:
    assert weekly_task.status(lambda argv: (0, '{"installed": false}', "")).installed is False

    never = weekly_task.status(
        lambda argv: (
            0,
            json.dumps(
                {
                    "installed": True,
                    "state": "Ready",
                    "next": "",
                    "last": "1999-11-30T00:00:00",
                    "result": weekly_task.NEVER_RUN,
                }
            ),
            "",
        )
    )
    assert never.last_run is None  # Task Scheduler's 1999 "never" value
    assert never.last_result_text == "not run yet"


def test_remove_reports_whether_a_task_was_there() -> None:
    assert weekly_task.remove(lambda argv: (0, "removed", "")) is True
    assert weekly_task.remove(lambda argv: (0, "not-installed", "")) is False
    calls: list[list[str]] = []
    weekly_task.remove(lambda argv: (calls.append(list(argv)), (0, "removed", ""))[1])
    assert "Unregister-ScheduledTask" in calls[0][-1]
    assert "-TaskName 'CivicCast Brief weekly'" in calls[0][-1]


def test_a_powershell_failure_is_shown_plainly_and_never_leaks_a_secret() -> None:
    def runner(argv):
        return 1, "", "Register-ScheduledTask : Access is denied.\nAt line:6 char:1"

    with pytest.raises(weekly_task.WeeklyTaskError) as caught:
        weekly_task.install("python.exe", "C:\\cities", runner)
    text = str(caught.value)
    assert "Access is denied." in text
    assert "\n" not in text  # one plain line
    assert "code 1" in text


def test_status_of_unreadable_output_is_a_plain_error() -> None:
    with pytest.raises(weekly_task.WeeklyTaskError):
        weekly_task.status(lambda argv: (0, "not json", ""))
