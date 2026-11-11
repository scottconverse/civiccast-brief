"""Install or update the one outside program CivicCast Brief runs: yt-dlp.

``python -m civiccast_brief tools --test-video URL`` asks PyPI for the newest yt-dlp, installs it
into CivicCast Brief's own folder (``~/.civiccast-brief/record/runtimes/yt-dlp``, with ``uv``),
fetches the captions of the test video with it, and makes it the active version only when that
works. The version that was active stays installed, so a bad release never replaces a good one.

This is the private runtime copied in from TownRecord (:mod:`civiccast_brief.record.runtime`).
The speech code needs no install of its own: it ships inside CivicCast Brief.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from civiccast_brief.record.capture.probe import capture_probe
from civiccast_brief.record.config import Settings
from civiccast_brief.record.runtime import RuntimeManager, UpdateOutcome

TEST_VIDEO_ENV = "CIVICCAST_RECORD_TEST_VIDEO"


class ToolsError(RuntimeError):
    """The tools could not be installed, with the reason in one sentence."""


def test_video(explicit: str | None = None) -> str:
    """The video the new yt-dlp is tested against: the argument, else the setting."""
    url = (explicit or os.environ.get(TEST_VIDEO_ENV, "")).strip()
    if not url:
        raise ToolsError(
            "name a test video: any public YouTube meeting video with captions, as "
            f"--test-video URL or the {TEST_VIDEO_ENV} setting. A new yt-dlp is made active "
            "only after it fetches that video's captions."
        )
    return url


def install_ytdlp(
    url: str,
    *,
    root: Path | None = None,
    client: httpx.Client | None = None,
    manager: RuntimeManager | None = None,
) -> UpdateOutcome:
    """Install the newest yt-dlp if it is newer than the active one and passes the test."""
    manager = manager or RuntimeManager(root=root or Settings.from_env().runtime_root)
    probe = capture_probe(test_video_url=url)
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        return manager.check_and_update(url, probe, client=client)
    finally:
        if own_client:
            client.close()
