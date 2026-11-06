"""Caption capture through TownRecord's own entry points.

This file is run by TownRecord's interpreter, isolated, and is never imported by civiccast_brief:

    <townrecord-venv>\\Scripts\\python.exe -I townrecord_bridge.py <video_id> <work_dir>

It builds the caption command with ``townrecord.capture.command.capture_argv`` (the spec 8.3
command: auto-captions only, srv3 first, no media download), runs it with
``townrecord.capture.command.run_capture`` and ``townrecord.proc.run``, and parses the file with
``townrecord.captions.parse.parse_srv3``. TownRecord's job queue and database are not used: a
single caption fetch needs neither.

Prints one JSON object on stdout: the video info, the caption file used, and the segments. On a
failed run it prints a JSON object with ``"error"`` and exits 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from townrecord import proc
from townrecord.captions.parse import parse_srv3, parse_vtt
from townrecord.capture import command

TIMEOUT_S = 900


def _fail(message: str, **extra: object) -> int:
    print(json.dumps({"error": message, **extra}, ensure_ascii=False))
    return 1


def _caption_file(work: Path, video_id: str) -> Path | None:
    for suffix in ("srv3", "vtt"):
        for candidate in sorted(work.glob(f"{video_id}*.{suffix}")):
            if candidate.stat().st_size > 0:
                return candidate
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return _fail("usage: townrecord_bridge.py <video_id> <work_dir>")
    video_id, work = argv[1], Path(argv[2])
    work.mkdir(parents=True, exist_ok=True)

    caption = _caption_file(work, video_id)
    reused = caption is not None
    if caption is None:
        url = command.WATCH_URL.format(platform_video_id=video_id)
        argv_out = command.capture_argv(
            interpreter=sys.executable,
            work_dir=work,
            archive=work / "download-archive.txt",
            url=url,
        )
        try:
            result = command.run_capture(proc.run, argv_out, timeout_s=TIMEOUT_S)
        except (proc.ProcessFailed, proc.ProcessTimedOut) as exc:
            return _fail(str(exc))
        if not result.ok:
            marker = command.bot_check_marker(result.stderr)
            return _fail(
                "yt-dlp failed",
                returncode=result.returncode,
                stderr_tail=result.last_stderr_line(),
                bot_check=marker,
            )
        caption = _caption_file(work, video_id)
        if caption is None:
            return _fail("yt-dlp finished but wrote no caption file (no English captions?)")

    data = caption.read_bytes()
    parser = parse_srv3 if caption.suffix == ".srv3" else parse_vtt
    segments = parser(data)

    info: dict[str, object] = {}
    info_file = work / f"{video_id}.info.json"
    if info_file.exists():
        raw = json.loads(info_file.read_text(encoding="utf-8"))
        info = {
            key: raw.get(key)
            for key in ("id", "title", "duration", "upload_date", "webpage_url", "channel")
        }

    print(
        json.dumps(
            {
                "video_id": video_id,
                "caption_file": str(caption),
                "caption_reused": reused,
                "info": info,
                "segments": [
                    {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text} for s in segments
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
