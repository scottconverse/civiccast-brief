"""Story card pictures: a frame from the meeting media, or a topic illustration."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from site_support import BASE_URL, FIXTURES, NOW

from civiccast_brief.site import graphics, stills
from civiccast_brief.site.build import build_site

MEDIA = "https://media.example.org/superior/meeting.mp4"
JPEG = b"\xff\xd8\xff\xe0 frame"


def _completed(stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


def _probe(blank: bool) -> bytes:
    if blank:
        return bytes([9, 9, 9]) * (stills.PROBE_SIZE[0] * stills.PROBE_SIZE[1])
    row = bytes([20, 40, 90] + [200, 120, 30] * (stills.PROBE_SIZE[0] - 1))
    return row * stills.PROBE_SIZE[1]


def _episodes(tmp_path: Path, *, video_url: str | None, media_url: str | None = None) -> Path:
    """The fixture episodes plus a weekly roundup story with no audio."""
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    story = {
        "id": "2026-11-03-example-weekly-roundup",
        "city": "example",
        "approved": True,
        "approved_by": "editor desk, 2026-11-04T09:00:00-07:00",
        "board": "Weekly roundup",
        "meeting_title": "Weekly roundup",
        "meeting_date": "2026-11-03",
        "published": "2026-11-04T09:00:00-07:00",
        "video_url": video_url,
        "media_url": media_url,
        "recap": {
            "claims": [
                {
                    "id": "c1",
                    "topic": "Budget",
                    "text": "The council passed the budget.",
                    "timestamp": "0:10",
                }
            ]
        },
        "article": {
            "headline": "Weekly roundup: the budget and the bus",
            "dek": "",
            "topics": ["budget"],
            "sections": [
                {
                    "heading": None,
                    "paragraphs": [
                        {
                            "text": "The council passed the budget.",
                            "cites": [{"claim": 0, "start_s": 600}],
                        }
                    ],
                }
            ],
        },
    }
    (episodes / "stories").mkdir()
    (episodes / "stories" / f"{story['id']}.json").write_text(json.dumps(story), "utf-8")
    return episodes


def test_the_frame_comes_from_the_lead_cited_moment() -> None:
    assert stills.frame_candidates(600, 7200)[0] == 600.0
    assert stills.frame_candidates(None, 7200)[0] == 720.0  # a tenth of the way in
    # Fallbacks move forward from the lead moment and stay inside the video.
    assert stills.frame_candidates(600, 7200)[1:3] == [645.0, 720.0]
    assert all(0 <= seconds <= 7199 for seconds in stills.frame_candidates(7190, 7200))


def test_a_blank_frame_is_skipped_for_the_next_moment() -> None:
    seen: list[float] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        if argv[0] == "ffprobe":
            return _completed(b"600\n")
        if "rawvideo" in argv:
            seconds = float(argv[argv.index("-ss") + 1])
            seen.append(seconds)
            return _completed(_probe(blank=seconds < 120))
        if "image2pipe" in argv:
            assert float(argv[argv.index("-ss") + 1]) == 145.0
            return _completed(JPEG)
        return _completed(returncode=1)

    assert stills.media_still(MEDIA, 100, runner=runner) == JPEG
    assert seen == [100.0, 145.0]  # the blank first moment was skipped


def test_looks_blank_catches_one_colour_frames() -> None:
    assert stills.looks_blank(_probe(blank=True))
    assert not stills.looks_blank(_probe(blank=False))
    assert stills.looks_blank(b"")


def test_every_topic_has_its_own_illustration_and_others_fall_back() -> None:
    for slug in (
        "budget",
        "transit",
        "utilities",
        "public-comment",
        "government",
        "land-use",
        "parks",
        "public-safety",
    ):
        assert graphics.graphic_slug(slug) == slug
    assert graphics.graphic_slug("housing") == "housing"
    assert graphics.graphic_slug("something-new") == graphics.FALLBACK
    assert graphics.asset_path("something-new") == "/assets/topics/civic.svg"
    svg = graphics.svg("budget")
    assert "<svg" in svg and "https://" not in svg and "<image" not in svg


def test_a_roundup_with_no_video_gets_a_topic_graphic(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site(_episodes(tmp_path, video_url=None), out, BASE_URL, now=NOW)
    home = (out / "index.html").read_text(encoding="utf-8")
    assert 'alt="Illustration of budget and taxes"' in home
    assert "/assets/topics/budget.svg" in home
    assert (out / "assets/topics/budget.svg").is_file()
    assert "thumb block" not in home


def test_direct_media_gets_a_frame_cached_and_served_from_the_site(tmp_path: Path) -> None:
    episodes = _episodes(
        tmp_path, video_url="https://www.youtube.com/watch?v=not-a-real-id", media_url=MEDIA
    )
    out = tmp_path / "site"
    calls: list[tuple[str, float | None]] = []

    def fetch_media(url: str, seconds: float | None) -> bytes:
        calls.append((url, seconds))
        return JPEG

    build_site(episodes, out, BASE_URL, now=NOW, fetch_media=fetch_media)
    home = (out / "index.html").read_text(encoding="utf-8")
    key = stills.media_key(MEDIA)
    assert calls == [(MEDIA, 600.0)]
    assert f'src="/assets/stills/{key}.jpg"' in home
    assert (out / "assets/stills" / f"{key}.jpg").read_bytes() == JPEG
    assert (episodes / "stills" / f"{key}.jpg").is_file()  # kept for the next build


def test_a_build_with_no_ffmpeg_still_works_and_uses_the_graphic(tmp_path: Path) -> None:
    episodes = _episodes(tmp_path, video_url=None, media_url=MEDIA)
    out = tmp_path / "site"

    def no_ffmpeg(url: str, seconds: float | None) -> bytes | None:
        # What media_still returns when the machine has no ffmpeg.
        return stills.media_still(url, seconds, runner=lambda argv: None)

    report = build_site(episodes, out, BASE_URL, now=NOW, fetch_media=no_ffmpeg)
    home = (out / "index.html").read_text(encoding="utf-8")
    assert report.stories == 1
    assert "/assets/topics/budget.svg" in home
    assert "/assets/stills/" not in home
