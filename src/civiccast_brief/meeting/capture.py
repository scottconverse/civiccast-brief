"""Caption capture, through TownRecord's caption command in TownRecord's own venv.

TownRecord stays a separate install: its dependency tree is not taken into civiccast_brief. The
bridge (townrecord_bridge.py) runs with ``-I`` in that venv and prints JSON, which is read here.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BRIDGE = Path(__file__).with_name("townrecord_bridge.py")
TOWNRECORD_PYTHON_ENV = "CIVICCAST_TOWNRECORD_PYTHON"
DEFAULT_TOWNRECORD_PYTHON = r"C:\dev\civiccast-brief-scratch\townrecord-venv\Scripts\python.exe"
BRIDGE_TIMEOUT_S = 1000


class CaptureError(RuntimeError):
    """The caption fetch failed, with the reason from TownRecord's command."""


@dataclass
class Capture:
    video_id: str
    caption_file: Path
    caption_reused: bool
    info: dict[str, Any]
    segments: list[dict[str, Any]] = field(default_factory=list)


def townrecord_python(explicit: str | None = None) -> str:
    return explicit or os.environ.get(TOWNRECORD_PYTHON_ENV) or DEFAULT_TOWNRECORD_PYTHON


def capture_captions(video_id: str, work_dir: Path, *, python: str | None = None) -> Capture:
    """Fetch the auto-caption track for one YouTube video id into ``work_dir``.

    Reuses a caption file already in ``work_dir`` (the bridge checks), so a second run does not
    call YouTube again.
    """
    if not video_id.replace("-", "").replace("_", "").isalnum():
        raise CaptureError(f"not a YouTube video id: {video_id!r}")
    work_dir.mkdir(parents=True, exist_ok=True)
    interpreter = townrecord_python(python)
    try:
        done = subprocess.run(
            [interpreter, "-I", str(BRIDGE), video_id, str(work_dir)],
            capture_output=True,
            timeout=BRIDGE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError(f"could not run TownRecord's caption command: {exc}") from exc
    stdout = done.stdout.decode("utf-8", errors="replace").strip().splitlines()
    payload = json.loads(stdout[-1]) if stdout else {"error": "no output from the bridge"}
    if done.returncode != 0 or "error" in payload:
        raise CaptureError(f"caption fetch failed for {video_id}: {json.dumps(payload)}")
    return Capture(
        video_id=video_id,
        caption_file=Path(payload["caption_file"]),
        caption_reused=bool(payload["caption_reused"]),
        info=dict(payload.get("info") or {}),
        segments=list(payload["segments"]),
    )


def caption_origin(work_dir: Path, video_id: str) -> dict[str, Any]:
    """Whether YouTube's own track or its automatic captions were used, from yt-dlp's info file."""
    info_file = work_dir / f"{video_id}.info.json"
    if not info_file.exists():
        return {"kind": "unknown", "reason": "no info file"}
    raw = json.loads(info_file.read_text(encoding="utf-8"))
    manual = "en" in (raw.get("subtitles") or {})
    return {"kind": "uploaded" if manual else "youtube_auto_captions"}
