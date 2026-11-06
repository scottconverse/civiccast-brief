"""Calls to ffmpeg and ffprobe through subprocess. Both tools must be on PATH."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from civiccast_brief.audio.errors import ToolError


def tools_available() -> bool:
    """True when ffmpeg and ffprobe are both on PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(argv: list[str], what: str) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"{argv[0]} is not on PATH; install ffmpeg") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise ToolError(f"{what} failed with exit code {proc.returncode}:\n{tail}")
    return proc


def run_ffmpeg(args: list[str], what: str = "ffmpeg") -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with the given arguments. Stderr is returned, because loudness is printed there."""
    return _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args], what)


def probe_duration(path: Path) -> float:
    """Length of a media file in seconds, as ffprobe reports it."""
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        f"ffprobe {path.name}",
    )
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise ToolError(f"ffprobe reported no duration for {path.name}") from exc
