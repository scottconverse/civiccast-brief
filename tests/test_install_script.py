"""install.ps1 calls the program the way the program reads its commands.

The installer is PowerShell, so a renamed command, flag or extra would only show when someone
installs on a new machine. These tests read the calls out of the script and give them to the
real parsers and to pyproject.toml.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from civiccast_brief import __main__ as cli
from civiccast_brief.review import cli as review_cli

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.ps1"
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_the_extras_it_installs_are_in_pyproject():
    extras = set(re.findall(r"'--extra', '([a-z]+)'", TEXT))
    assert extras == {"browser", "diarize"}
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert extras <= set(declared["project"]["optional-dependencies"])


def test_the_tools_call_is_one_the_program_reads(monkeypatch):
    call = re.search(r"-m civiccast_brief (tools --test-video) \$TestVideo", TEXT)
    assert call, "install.ps1 no longer runs the tools step"
    seen = {}

    def fake_tools(args):
        seen["video"] = args.test_video
        return 0

    monkeypatch.setattr(cli, "_tools", fake_tools)
    default = re.search(r"\[string\]\$TestVideo = '([^']+)'", TEXT).group(1)
    assert cli.main([*call.group(1).split(), default]) == 0
    assert seen["video"] == default


def test_the_desk_starter_is_a_command_the_desk_reads():
    line = re.search(r"-m civiccast_brief\.review (serve --db) `\"%WORK%\\desk\.db`\"", TEXT)
    assert line, "desk.cmd no longer starts the desk"
    args = review_cli.build_parser().parse_args([*line.group(1).split(), "desk.db"])
    assert args.command == "serve"
    assert f"http://127.0.0.1:{args.port}/" in TEXT


def test_the_city_setup_it_points_to_is_a_command_the_program_reads():
    hint = re.search(
        r"civiccast_brief (city find) --name `\"Your City`\" --state CO --dry-run", TEXT
    )
    assert hint, "install.ps1 no longer points to city find"
    from civiccast_brief.citybuild import cli as city_cli

    parser = argparse.ArgumentParser()
    city_cli.add_commands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [*hint.group(1).split(), "--name", "Your City", "--state", "CO", "--dry-run"]
    )
    assert args.dry_run is True


@pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is for Windows")
@pytest.mark.skipif(shutil.which("powershell") is None, reason="needs Windows PowerShell")
def test_it_parses_and_plans_in_windows_powershell():
    # Windows PowerShell 5.1 is what a new machine has; PowerShell 7 syntax would fail there.
    out = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-PlanOnly",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    steps = [line.split(". ", 1)[1] for line in out.stdout.splitlines() if line.strip()]
    assert steps == [
        "Get the code",
        "uv",
        "Python packages",
        "ffmpeg",
        "yt-dlp",
        "Chrome and Ollama",
        "desk.cmd",
    ]
