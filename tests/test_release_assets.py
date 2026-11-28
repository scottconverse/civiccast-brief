"""release-assets.ps1 builds a pinned installer for one release, and the workflow attaches it.

The script is PowerShell, so these tests run it with the PowerShell this machine has and read
what it wrote. No network.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "release-assets.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "release-assets.yml"
INSTALL_PS1 = ROOT / "install.ps1"
INSTALL_CMD = ROOT / "install.cmd"
TAG = "v9.9.9"
ZIP_NAME = f"civiccast-brief-install-{TAG}.zip"

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="needs powershell or pwsh on PATH")


@pytest.fixture(scope="module")
def assets(tmp_path_factory):
    out = tmp_path_factory.mktemp("assets")
    subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Tag",
            TAG,
            "-OutDir",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return out


@needs_powershell
def test_it_writes_the_four_files(assets):
    for name in ("install.ps1", "install.cmd", ZIP_NAME, "SHA256SUMS.txt"):
        assert (assets / name).is_file(), name


@needs_powershell
def test_the_pinned_script_differs_only_in_the_ref_line(assets):
    pinned = (assets / "install.ps1").read_text(encoding="utf-8").splitlines()
    original = INSTALL_PS1.read_text(encoding="utf-8").splitlines()
    stripped = [line.strip() for line in pinned]
    assert f"[string]$Ref = '{TAG}'," in stripped
    assert "[string]$Ref = 'main'," not in stripped
    assert len(pinned) == len(original)
    changed = [(a, b) for a, b in zip(original, pinned) if a != b]
    assert len(changed) == 1, changed
    assert changed[0][0].strip() == "[string]$Ref = 'main',"
    assert changed[0][1].strip() == f"[string]$Ref = '{TAG}',"


@needs_powershell
def test_the_zip_holds_the_two_installer_files(assets):
    with zipfile.ZipFile(assets / ZIP_NAME) as archive:
        assert sorted(archive.namelist()) == ["install.cmd", "install.ps1"]
        assert archive.read("install.ps1") == (assets / "install.ps1").read_bytes()
        assert archive.read("install.cmd") == INSTALL_CMD.read_bytes()


@needs_powershell
def test_the_sums_match_the_files(assets):
    lines = (assets / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    names = []
    for line in lines:
        digest, name = line.split("  ")
        assert digest == hashlib.sha256((assets / name).read_bytes()).hexdigest(), name
        assert digest == digest.lower()
        names.append(name)
    assert names == sorted(names)
    assert sorted(names) == sorted(["install.cmd", "install.ps1", ZIP_NAME])


def test_the_workflow_runs_for_releases_dispatches_and_pulls():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^on:\s*$", text, re.MULTILINE)
    for trigger in ("release:", "workflow_dispatch:", "pull_request:"):
        assert re.search(rf"^  {trigger}", text, re.MULTILINE), trigger
    assert re.search(r"^permissions:\s*\n\s+contents: write\s*$", text, re.MULTILINE)
