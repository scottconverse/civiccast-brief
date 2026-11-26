"""The Windows scheduled task: exact PowerShell, status parsing, and plain failures."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from civiccast_brief.review import weekly_task
from civiccast_brief.speech import windows_process

PREFIX = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]


def test_install_builds_the_exact_powershell_argv_for_the_current_user() -> None:
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0, "", ""

    weekly_task.install(r"C:\Python\python.exe", r"C:\cities", runner)

    script = r"""$ErrorActionPreference = 'Stop'
$me = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'C:\Python\python.exe' -Argument '-m civiccast_brief.review run-due --cities "C:\cities"'
$logon = New-ScheduledTaskTrigger -AtLogOn -User $me
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'CivicCast Brief weekly' -Action $action -Trigger @($logon, $hourly) -Settings $settings -Principal $principal -Force | Out-Null"""
    assert calls == [[*PREFIX, script]]


def test_the_trigger_names_this_user_and_needs_no_administrator() -> None:
    script = weekly_task.install_script("python.exe", r"C:\cities")

    # A trigger with no -User means any user's logon, which a non-elevated user may not set up.
    assert "-AtLogOn -User $me" in script
    assert "-Principal $principal" in script
    assert "-LogonType Interactive -RunLevel Limited" in script
    # Task Scheduler rejects P99999999DT23H59M59S; no duration means it repeats with no end.
    assert "RepetitionDuration" not in script
    assert "-RepetitionInterval (New-TimeSpan -Hours 1)" in script


def test_the_task_may_start_and_keep_running_on_battery() -> None:
    script = weekly_task.install_script("python.exe", r"C:\cities")

    # Windows otherwise keeps its defaults, which never start the hour's run on battery.
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script


def test_a_pythonw_beside_python_runs_the_task_with_no_console_window(tmp_path) -> None:
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0, "", ""

    weekly_task.install(str(tmp_path / "python.exe"), r"C:\cities", runner)

    assert f"-Execute '{tmp_path / 'pythonw.exe'}'" in calls[0][-1]
    assert "python.exe'" not in calls[0][-1]


def test_without_a_pythonw_beside_it_the_given_python_is_used(tmp_path) -> None:
    (tmp_path / "python.exe").write_text("")
    python = str(tmp_path / "python.exe")

    assert weekly_task.quiet_python(python) == python
    assert weekly_task.quiet_python(r"C:\Python\python3.exe") == r"C:\Python\python3.exe"


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


def test_status_reads_the_cities_folder_from_the_task_arguments() -> None:
    status = weekly_task.status(
        lambda argv: (
            0,
            json.dumps(
                {
                    "installed": True,
                    "state": "Ready",
                    "next": "",
                    "last": "",
                    "result": weekly_task.NEVER_RUN,
                    "arguments": '-m civiccast_brief.review run-due --cities "C:\\x\\cities"',
                }
            ),
            "",
        )
    )

    assert status.cities == r"C:\x\cities"


def test_other_desk_names_another_folder_and_nothing_otherwise(tmp_path) -> None:
    here = tmp_path / "here"
    elsewhere = tmp_path / "elsewhere"

    assert (
        weekly_task.other_desk(weekly_task.TaskStatus(installed=True, cities=str(here)), here) == ""
    )
    assert weekly_task.other_desk(
        weekly_task.TaskStatus(installed=True, cities=str(elsewhere)), here
    ) == str(elsewhere)
    assert weekly_task.other_desk(weekly_task.TaskStatus(installed=False), here) == ""


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


def _hidable(monkeypatch, platform: str) -> None:
    """Make the helper believe a windowless pythonw parent is running."""
    monkeypatch.setattr(windows_process.sys, "platform", platform)
    monkeypatch.setattr(windows_process.sys, "executable", r"C:\Python\pythonw.exe")


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW exists only on Windows")
def test_powershell_runs_with_its_console_hidden_on_windows(monkeypatch) -> None:
    _hidable(monkeypatch, "win32")
    calls: list[dict[str, object]] = []

    def fake_run(argv, **kwargs):
        calls.append(dict(kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(weekly_task.subprocess, "run", fake_run)

    weekly_task.run_powershell(["powershell.exe", "-Command", "x"])

    assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_powershell_adds_no_flag_on_other_systems(monkeypatch) -> None:
    _hidable(monkeypatch, "linux")
    calls: list[dict[str, object]] = []

    def fake_run(argv, **kwargs):
        calls.append(dict(kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(weekly_task.subprocess, "run", fake_run)

    weekly_task.run_powershell(["powershell.exe", "-Command", "x"])

    assert "creationflags" not in calls[0]


def test_status_of_unreadable_output_is_a_plain_error() -> None:
    with pytest.raises(weekly_task.WeeklyTaskError):
        weekly_task.status(lambda argv: (0, "not json", ""))
