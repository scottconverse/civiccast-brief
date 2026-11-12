"""Caption capture for one YouTube meeting video.

This runs in-process with the caption code copied into CivicCast Brief from TownRecord
(:mod:`civiccast_brief.record`): ``record.capture.command`` builds and runs the spec 8.3 command
(auto-captions only, srv3 first, no media download) and ``record.captions.parse`` reads the file.
Nothing outside CivicCast Brief needs to be installed.

yt-dlp itself runs from the private runtime CivicCast Brief installs for it
(``record.runtime``), under ``~/.civiccast-brief/record/runtimes/yt-dlp``. That keeps yt-dlp
current without touching the Python CivicCast Brief runs in.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from civiccast_brief.meeting import panel
from civiccast_brief.record import proc
from civiccast_brief.record.captions.parse import parse_srv3, parse_vtt
from civiccast_brief.record.capture import command, sidecar

CAPTION_TIMEOUT_S = 900

#: Runs one child: ``runner(argv, timeout_s=..., env=...)`` -> ``proc.ProcessResult``.
Runner = Callable[..., proc.ProcessResult]


class CaptureError(RuntimeError):
    """The caption fetch failed, with the reason from the caption command."""


@dataclass
class Capture:
    video_id: str
    caption_file: Path
    caption_reused: bool
    info: dict[str, Any]
    segments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class YtDlp:
    """The Python that runs ``-m yt_dlp``, and the JavaScript runtime it is told to use."""

    python: str
    js_runtime: str | None = None


def ytdlp(explicit: str | None = None) -> YtDlp:
    """The yt-dlp to run: ``explicit`` when given, else CivicCast Brief's private runtime.

    A missing runtime is a plain error that says to run setup, never a quiet fallback to
    whatever Python is on the machine.
    """
    if explicit:
        return YtDlp(python=explicit)
    from civiccast_brief.record.config import Settings
    from civiccast_brief.record.runtime import RuntimeManager, RuntimeNotInstalled

    manager = RuntimeManager(root=Settings.from_env().runtime_root)
    try:
        return YtDlp(python=manager.interpreter(), js_runtime=manager.javascript())
    except RuntimeNotInstalled as exc:
        raise CaptureError(f"yt-dlp is not set up yet: {exc}") from exc


def _caption_file(work: Path, video_id: str) -> Path | None:
    """The publisher's own track (``<id>.en.<ext>``) first, then any other, srv3 before vtt."""
    for suffix in ("srv3", "vtt"):
        uploaded = work / f"{video_id}.{sidecar.UPLOADED_TRACK}.{suffix}"
        if uploaded.exists() and uploaded.stat().st_size > 0:
            return uploaded
    for suffix in ("srv3", "vtt"):
        for candidate in sorted(work.glob(f"{video_id}*.{suffix}")):
            if candidate.stat().st_size > 0:
                return candidate
    return None


def _info(work: Path, video_id: str) -> dict[str, Any]:
    info_file = work / f"{video_id}.info.json"
    if not info_file.exists():
        return {}
    raw = json.loads(info_file.read_text(encoding="utf-8"))
    keys = ("id", "title", "duration", "upload_date", "webpage_url", "channel")
    return {key: raw.get(key) for key in keys}


def _run(runner: Runner, argv: list[str], video_id: str) -> proc.ProcessResult:
    try:
        return command.run_capture(runner, argv, timeout_s=CAPTION_TIMEOUT_S)
    except (proc.ProcessFailed, proc.ProcessTimedOut) as exc:
        raise CaptureError(f"could not run the caption command for {video_id}: {exc}") from exc


def capture_captions(
    video_id: str,
    work_dir: Path,
    *,
    python: str | None = None,
    runner: Runner = proc.run,
    browser: panel.PanelBrowser | None = None,
) -> Capture:
    """Fetch the auto-caption track for one YouTube video id into ``work_dir``.

    Reuses a caption file already in ``work_dir``, so a second run does not call YouTube again.
    ``python`` names the interpreter that runs yt-dlp; by default it is the private runtime.
    When yt-dlp gets no captions, the watch page's "Show transcript" panel is read in Chrome
    (spec 8.4.2); ``browser`` stands in for Chrome in tests.
    """
    if not video_id.replace("-", "").replace("_", "").isalnum():
        raise CaptureError(f"not a YouTube video id: {video_id!r}")
    # yt-dlp joins a relative output name onto --paths, so a relative folder would get the
    # files one level too deep (raw/runs/x/raw/...). An absolute folder avoids that.
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    caption = _caption_file(work_dir, video_id) or _panel_kept(work_dir, video_id)
    reused = caption is not None
    if caption is None:
        try:
            caption = _ytdlp_captions(video_id, work_dir, python, runner)
        except CaptureError as exc:
            try:
                caption = panel.read_panel(video_id, work_dir, browser)
            except panel.PanelError as panel_exc:
                raise CaptureError(f"{exc}; then the Show transcript panel: {panel_exc}") from exc

    if caption.name.endswith(panel.SUFFIX):
        segments = panel.load(caption)
    else:
        parser = parse_srv3 if caption.suffix == ".srv3" else parse_vtt
        segments = [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
            for s in parser(caption.read_bytes())
        ]
    return Capture(
        video_id=video_id,
        caption_file=caption,
        caption_reused=reused,
        info=_info(work_dir, video_id),
        segments=segments,
    )


def _panel_kept(work: Path, video_id: str) -> Path | None:
    path = panel.panel_file(work, video_id)
    return path if path.exists() else None


def _ytdlp_captions(video_id: str, work_dir: Path, python: str | None, runner: Runner) -> Path:
    """yt-dlp's caption file for the video, or :class:`CaptureError` with its reason."""
    tool = ytdlp(python)
    argv = command.capture_argv(
        interpreter=tool.python,
        work_dir=work_dir,
        archive=work_dir / "download-archive.txt",
        url=command.WATCH_URL.format(platform_video_id=video_id),
        js_runtime=tool.js_runtime,
    )
    result = _run(runner, argv, video_id)
    info_file = work_dir / f"{video_id}.info.json"
    if (
        result.ok
        and info_file.exists()
        and sidecar.has_uploaded_track(json.loads(info_file.read_text(encoding="utf-8")))
    ):
        # Spec 8.3's second request: the town uploaded its own captions.
        result = _run(runner, command.uploaded_argv(argv), video_id)
    if not result.ok:
        detail = {
            "returncode": result.returncode,
            "stderr_tail": result.last_stderr_line(),
            "bot_check": command.bot_check_marker(result.stderr),
        }
        raise CaptureError(f"caption fetch failed for {video_id}: yt-dlp failed {detail}")
    caption = _caption_file(work_dir, video_id)
    if caption is None:
        raise CaptureError(
            f"caption fetch failed for {video_id}: yt-dlp finished but wrote no caption file "
            "(no English captions?)"
        )
    return caption


def caption_origin(work_dir: Path, video_id: str) -> dict[str, Any]:
    """Whether YouTube's own track, its automatic captions or the transcript panel was used."""
    if _caption_file(work_dir, video_id) is None and _panel_kept(work_dir, video_id):
        return {"kind": "youtube_transcript_panel"}
    info_file = work_dir / f"{video_id}.info.json"
    if not info_file.exists():
        return {"kind": "unknown", "reason": "no info file"}
    raw = json.loads(info_file.read_text(encoding="utf-8"))
    manual = "en" in (raw.get("subtitles") or {})
    return {"kind": "uploaded" if manual else "youtube_auto_captions"}
