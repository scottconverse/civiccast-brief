"""ops\\stage.ps1 refuses the things it must refuse, and its plan writes nothing.

The safety checks live in PowerShell, so these tests run them through pwsh rather than
re-implementing the rules in Python. The full script is given bad arguments; the pure
check functions are dot-sourced and called directly.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "ops" / "stage.ps1"
LIB = ROOT / "ops" / "stage-lib.ps1"
SCRATCH = Path(r"C:\dev\civiccast-brief-scratch")
PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or PWSH is None,
    reason="ops\\stage.ps1 is a Windows PowerShell script",
)

PLAN = (
    "-Plan",
    "-Repo",
    str(ROOT),
    "-Python",
    sys.executable,
)


def run_stage(
    *args: str, timeout: int = 180, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(STAGE), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, **(env or {})},
    )


def run_pwsh(
    code: str, timeout: int = 60, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, **(env or {})},
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_fake_source(root: Path) -> Path:
    """A tiny stand-in for a city's live working folder."""
    source = root / "source"
    (source / "cities" / "superior").mkdir(parents=True)
    (source / "runs").mkdir()
    episodes = source / "live" / "episodes"
    episodes.mkdir(parents=True)
    (episodes / "2026-11-18-city-council.json").write_text(
        '{"url": "https://media.civiccastbrief.org/superior/x.mp3"}', encoding="utf-8"
    )
    media = source / "live" / "media"
    media.mkdir(parents=True)
    (media / "x.mp3").write_bytes(b"ID3stub")
    (source / "desk.db").write_bytes(b"")
    return source


def make_weekly_stub(root: Path, exit_code: int, problem: str) -> Path:
    """A stand-in for `civiccast_brief.weekly run`: builds a site, then exits non-zero."""
    stub = root / "weekly-stub.ps1"
    stub.write_text(
        "$site = Join-Path (Get-Location) 'live\\site'\n"
        "New-Item -ItemType Directory -Force -Path (Join-Path $site 'episodes\\ep') | Out-Null\n"
        "Set-Content -LiteralPath (Join-Path $site 'index.html') "
        "-Value '<html><body>stub</body></html>' -Encoding utf8\n"
        "Set-Content -LiteralPath (Join-Path $site 'episodes\\ep\\index.html') "
        "-Value '<html>ep</html>' -Encoding utf8\n"
        f"Write-Output 'source problem: {problem}'\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    return stub


def test_the_script_refuses_port_3000():
    out = run_stage(*PLAN, "-Tier", "Dev", "-City", "superior", "-Port", "3000")
    assert out.returncode != 0
    assert "3000" in (out.stdout + out.stderr)


def test_the_script_refuses_a_pages_project_that_is_not_the_test_project():
    out = run_stage(
        *PLAN, "-Tier", "Test", "-City", "superior", "-PagesProject", "civiccast-brief-superior"
    )
    assert out.returncode != 0
    assert "civiccast-brief-test" in (out.stdout + out.stderr)


def test_the_script_refuses_an_r2_prefix_outside_test(tmp_path: Path):
    source = make_fake_source(tmp_path)
    out = run_stage(
        *PLAN,
        "-Tier",
        "Test",
        "-City",
        "superior",
        "-R2Prefix",
        "superior/",
        "-Source",
        str(source),
        "-OutRoot",
        str(tmp_path / "out"),
    )
    assert out.returncode != 0
    assert "test/" in (out.stdout + out.stderr)


def test_the_script_refuses_a_stage_folder_inside_the_scratch_root():
    out = run_stage(
        *PLAN,
        "-Tier",
        "Dev",
        "-City",
        "superior",
        "-OutRoot",
        str(SCRATCH / "stage-test"),
    )
    assert out.returncode != 0
    assert "civiccast-brief-scratch" in (out.stdout + out.stderr)


def test_the_library_refuses_an_r2_key_outside_test():
    out = run_pwsh(
        f". '{LIB}'; "
        "try { Assert-StageR2Key 'superior/x.mp3'; exit 7 } "
        "catch { Write-Output $_.Exception.Message; exit 3 }"
    )
    assert out.returncode == 3, out.stdout + out.stderr
    assert "test/" in out.stdout


def test_the_library_refuses_a_pages_project_that_is_not_the_test_project():
    out = run_pwsh(
        f". '{LIB}'; "
        "try { Assert-StagePagesProject 'civiccast-brief-superior'; exit 7 } "
        "catch { Write-Output $_.Exception.Message; exit 3 }"
    )
    assert out.returncode == 3, out.stdout + out.stderr
    assert "civiccast-brief-test" in out.stdout


def test_the_library_refuses_while_a_weekly_task_is_running():
    out = run_pwsh(
        f". '{LIB}'; "
        "Assert-NoWeeklyTaskRunning -Tasks @("
        "[pscustomobject]@{ TaskName = 'CivicCast Brief weekly (Superior)'; State = 'Running' })"
    )
    assert out.returncode != 0
    assert "weekly" in (out.stdout + out.stderr).lower()


def test_the_library_accepts_the_test_project_test_prefix_and_a_ready_task():
    out = run_pwsh(
        f". '{LIB}'; "
        "Assert-StagePagesProject 'civiccast-brief-test'; "
        "Assert-StageR2Key 'test/superior/x.mp3'; "
        "Assert-NoWeeklyTaskRunning -Tasks @("
        "[pscustomobject]@{ TaskName = 'CivicCast Brief weekly (Superior)'; State = 'Ready' }); "
        "Write-Output 'ok'"
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "ok" in out.stdout


@pytest.mark.skipif(
    not (SCRATCH / "weekly-superior").is_dir(), reason="needs the Superior working folder"
)
def test_the_dev_plan_uses_the_test_tone_and_names_no_upload():
    out = run_stage(*PLAN, "-Tier", "Dev", "-City", "superior")
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert "--audio-engine fake" in text
    assert "http://127.0.0.1:3200" in text
    assert "wrangler" not in text.lower()
    assert "upload target:  none" in text


@pytest.mark.skipif(
    not (SCRATCH / "weekly-superior").is_dir(), reason="needs the Superior working folder"
)
def test_the_dev_plan_can_ask_for_real_voices():
    out = run_stage(*PLAN, "-Tier", "Dev", "-City", "superior", "-RealVoice")
    assert out.returncode == 0, out.stderr
    assert "--audio-engine gemini" in out.stdout
    assert "--audio-fallback-engine lfm3" in out.stdout


@pytest.mark.skipif(
    not (SCRATCH / "weekly-superior").is_dir(), reason="needs the Superior working folder"
)
def test_the_test_plan_names_only_the_test_project_and_test_prefix():
    out = run_stage(*PLAN, "-Tier", "Test", "-City", "superior")
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert "civiccast-brief-test" in text
    assert "branch superior" in text
    assert "civiccast-brief-media/test/superior/" in text
    assert "--audio-engine gemini" in text


@pytest.mark.skipif(
    not (SCRATCH / "weekly-real").is_dir(), reason="needs the Longmont working folder"
)
def test_longmont_reads_the_weekly_real_folder():
    out = run_stage(*PLAN, "-Tier", "Dev", "-City", "longmont")
    assert out.returncode == 0, out.stderr
    assert "weekly-real" in out.stdout


@pytest.mark.skipif(
    not (SCRATCH / "weekly-superior").is_dir(), reason="needs the Superior working folder"
)
def test_the_plan_creates_nothing(tmp_path: Path):
    out = run_stage(*PLAN, "-Tier", "Dev", "-City", "superior", "-OutRoot", str(tmp_path))
    assert out.returncode == 0, out.stderr
    assert list(tmp_path.iterdir()) == []


EPISODE = """{
  "id": "2026-11-18-city-council",
  "url": "https://media.civiccastbrief.org/superior/2026-11-18-city-council.mp3",
  "chapters_url": "https://media.civiccastbrief.org/superior/2026-11-18-city-council-chapters.json",
  "vtt_url": "https://media.civiccastbrief.org/superior/2026-11-18-city-council.vtt",
  "video_url": "https://www.youtube.com/watch?v=abc123",
  "article": {"pages": [{"url": "https://records.superiorcolorado.gov/packet.pdf"}]}
}
"""


@pytest.mark.parametrize(
    ("tier_media_base", "expected"),
    [
        ("http://127.0.0.1:3200/media", "http://127.0.0.1:3200/media/"),
        (
            "https://media.civiccastbrief.org/test/superior",
            "https://media.civiccastbrief.org/test/superior/",
        ),
    ],
)
def test_the_copy_rewrites_only_the_live_city_media_prefix(
    tmp_path: Path, tier_media_base: str, expected: str
):
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    (episodes / "episode.json").write_text(EPISODE, encoding="utf-8")
    (episodes / "other-city.json").write_text(
        EPISODE.replace("/superior/", "/longmont/"), encoding="utf-8"
    )
    (episodes / "notes.txt").write_text(
        "https://media.civiccastbrief.org/superior/x.mp3", encoding="utf-8"
    )

    out = run_pwsh(
        ". $env:STAGE_LIB; "
        "Set-StageEpisodeMediaBase -Folder $env:EPISODES -City superior "
        "-MediaBaseUrl $env:MEDIA_BASE",
        env={
            "STAGE_LIB": str(LIB),
            "EPISODES": str(episodes),
            "MEDIA_BASE": tier_media_base,
        },
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip().splitlines()[-1] == "1"

    text = (episodes / "episode.json").read_text(encoding="utf-8")
    live = "https://media.civiccastbrief.org/superior/"
    assert live not in text
    assert text.count(expected) == 3
    assert "https://www.youtube.com/watch?v=abc123" in text
    assert "https://records.superiorcolorado.gov/packet.pdf" in text
    assert text == EPISODE.replace(live, expected)
    # no BOM: the JSON reader the run uses treats one as a syntax error
    assert (episodes / "episode.json").read_bytes().startswith(b"{")
    # another city's prefix and non-JSON files are left alone
    assert "/longmont/" in (episodes / "other-city.json").read_text(encoding="utf-8")
    assert (episodes / "notes.txt").read_text(encoding="utf-8").startswith(live)


def test_the_guard_finds_the_live_address_in_every_built_page_type(tmp_path: Path):
    site = tmp_path / "site"
    (site / "episodes" / "x").mkdir(parents=True)
    (site / "index.html").write_text(
        '<a href="https://superior.civiccastbrief.org/episodes/x/">x</a>', encoding="utf-8"
    )
    (site / "feed.xml").write_text(
        "<link>https://superior.civiccastbrief.org/</link>"
        '<enclosure url="https://media.civiccastbrief.org/superior/x.mp3"/>',
        encoding="utf-8",
    )
    (site / "episodes" / "x" / "index.html").write_text(
        '<audio src="https://media.civiccastbrief.org/superior/x.mp3">', encoding="utf-8"
    )
    (site / "search.json").write_text(
        '["https://media.civiccastbrief.org/superior/x.mp3"]', encoding="utf-8"
    )
    (site / "clean.json").write_text(
        '["https://media.civiccastbrief.org/test/superior/x.mp3"]', encoding="utf-8"
    )
    (site / "notes.txt").write_text("https://superior.civiccastbrief.org/", encoding="utf-8")

    out = run_pwsh(
        ". $env:STAGE_LIB; @(Find-StageLiveAddress -Root $env:SITE -City superior).Count",
        env={"STAGE_LIB": str(LIB), "SITE": str(site)},
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip().splitlines()[-1] == "5"

    clean_out = run_pwsh(
        ". $env:STAGE_LIB; @(Find-StageLiveAddress -Root $env:SITE -City superior).Count",
        env={
            "STAGE_LIB": str(LIB),
            "SITE": str(tmp_path / "clean"),
        },
    )
    assert clean_out.returncode == 0
    assert clean_out.stdout.strip().splitlines()[-1] == "0"


def test_the_noindex_header_goes_inside_the_root_rule(tmp_path: Path):
    headers = tmp_path / "_headers"
    headers.write_text(
        "/*\n  X-Frame-Options: DENY\n\n/assets/*\n  Cache-Control: max-age=3600\n",
        encoding="utf-8",
    )
    out = run_pwsh(
        ". $env:STAGE_LIB; Set-StageNoindexHeader -Path $env:HEADERS",
        env={"STAGE_LIB": str(LIB), "HEADERS": str(headers)},
    )
    assert out.returncode == 0, out.stdout + out.stderr

    text = headers.read_text(encoding="utf-8")
    root_block = text.split("\n\n", 1)[0]
    assert root_block.startswith("/*\n")
    assert "  X-Robots-Tag: noindex" in root_block.splitlines()
    assert "  X-Frame-Options: DENY" in root_block.splitlines()
    assert "/assets/*\n  Cache-Control: max-age=3600\n" in text
    assert text.index("X-Robots-Tag") < text.index("/assets/*")
    assert text.count("X-Robots-Tag: noindex") == 1


def test_the_noindex_header_creates_the_root_rule_when_it_is_missing(tmp_path: Path):
    headers = tmp_path / "_headers"
    headers.write_text("/assets/*\n  Cache-Control: max-age=3600\n", encoding="utf-8")
    out = run_pwsh(
        ". $env:STAGE_LIB; Set-StageNoindexHeader -Path $env:HEADERS; "
        "Set-StageNoindexHeader -Path $env:HEADERS",
        env={"STAGE_LIB": str(LIB), "HEADERS": str(headers)},
    )
    assert out.returncode == 0, out.stdout + out.stderr

    text = headers.read_text(encoding="utf-8")
    assert text.startswith("/*\n  X-Robots-Tag: noindex\n")
    assert "/assets/*\n  Cache-Control: max-age=3600\n" in text
    # running it twice must not add a second header
    assert text.count("X-Robots-Tag: noindex") == 1


def test_the_run_verdict_collects_source_problem_lines(tmp_path: Path):
    log = tmp_path / "stage-run.log"
    log.write_text(
        "working on superior\n"
        "  source problem: no captions for 2026-11-18\n"
        "more output\n"
        "source problem: no meeting video for 2026-11-18\n",
        encoding="utf-8",
    )
    out = run_pwsh(
        ". $env:STAGE_LIB; "
        "$v = Get-StageRunVerdict -ExitCode 5 -LogPath $env:LOG; "
        'Write-Output "passed=$($v.Passed) code=$($v.ExitCode) count=$($v.Problems.Count)"; '
        "$v.Problems | ForEach-Object { Write-Output $_ }",
        env={"STAGE_LIB": str(LIB), "LOG": str(log)},
    )
    assert out.returncode == 0, out.stdout + out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines[0] == "passed=False code=5 count=2"
    assert "source problem: no captions for 2026-11-18" in lines
    assert "source problem: no meeting video for 2026-11-18" in lines


def test_a_failed_test_run_stops_before_upload_and_deploy(tmp_path: Path):
    source = make_fake_source(tmp_path)
    stub = make_weekly_stub(tmp_path, exit_code=4, problem="no captions for 2026-11-18")
    out_root = tmp_path / "stage"
    marker = tmp_path / "npx-called.txt"
    npx = tmp_path / "npx.cmd"
    npx.write_text(f'@echo off\r\necho called > "{marker}"\r\nexit /b 0\r\n')

    out = run_stage(
        "-Tier",
        "Test",
        "-City",
        "superior",
        "-Repo",
        str(ROOT),
        "-Python",
        sys.executable,
        "-Source",
        str(source),
        "-OutRoot",
        str(out_root),
        "-WeeklyCommand",
        str(stub),
        env={
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        },
    )
    text = out.stdout + out.stderr
    assert out.returncode != 0, text
    assert "exited 4" in text
    assert "stage-run.log" in text
    assert "source problem: no captions for 2026-11-18" in text
    # the upload and deploy steps were never reached
    assert "== upload changed media" not in text
    assert "== deploy" not in text
    assert not marker.exists(), "npx was called; upload or deploy ran"


def test_a_failed_dev_run_serves_but_never_passes(tmp_path: Path):
    source = make_fake_source(tmp_path)
    stub = make_weekly_stub(tmp_path, exit_code=3, problem="no captions for 2026-11-18")
    out_root = tmp_path / "stage"
    port = free_port()

    # The Dev server is left running, so it inherits any pipe. Send the script's
    # output to a file instead, or the read would wait for the server to close it.
    log = tmp_path / "stage-output.txt"
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        out = subprocess.run(
            [
                PWSH,
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(STAGE),
                "-Tier",
                "Dev",
                "-City",
                "superior",
                "-Port",
                str(port),
                "-Repo",
                str(ROOT),
                "-Python",
                sys.executable,
                "-Source",
                str(source),
                "-OutRoot",
                str(out_root),
                "-WeeklyCommand",
                str(stub),
            ],
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
    pid_files = list(out_root.glob("dev-superior-*/stage.pid"))
    pid = int(pid_files[0].read_text(encoding="ascii").strip()) if pid_files else None
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
        assert out.returncode != 0, text
        assert "NOT PASSED: weekly run exit 3" in text
        assert "source problem: no captions for 2026-11-18" in text
        assert "PASS: Dev stage" not in text
        assert "Stop-Process -Id" in text
        assert pid is not None and f"Stop-Process -Id {pid}" in text
        assert f"http://127.0.0.1:{port}/" in text
    finally:
        if pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
