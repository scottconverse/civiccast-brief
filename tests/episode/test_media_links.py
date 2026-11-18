"""A meeting outside YouTube never gets a youtube.com link."""

from __future__ import annotations

from civiccast_brief.article.writer import video_link
from civiccast_brief.episode.build import _video_url
from civiccast_brief.script.recap import Claim, Recap

CIVICCLERK = Recap(
    city="Superior",
    body="Town Council",
    meeting_date="October 26, 2026",
    video_id="civicclerk-superiorco-43",
    claims=(
        Claim(
            id="c01",
            topic="Budget",
            text="The council approved the budget.",
            timestamp="1:09:42",
            quote="the motion passes",
        ),
    ),
    media_url="https://cpmedia.azureedge.net/superiorco/abc.mp4",
)


def test_a_civicclerk_cite_links_its_own_media() -> None:
    url = video_link(CIVICCLERK, "1:09:42")
    assert url == "https://cpmedia.azureedge.net/superiorco/abc.mp4#t=4182"
    assert "youtube.com" not in url


def test_a_civicclerk_id_without_media_makes_no_link() -> None:
    recap = Recap(
        city="Superior",
        body="Town Council",
        meeting_date="October 26, 2026",
        video_id="civicclerk-superiorco-43",
        claims=CIVICCLERK.claims,
    )
    assert video_link(recap, "0:01:00") == ""


def test_a_youtube_cite_is_unchanged() -> None:
    recap = Recap(
        city="Longmont",
        body="City Council",
        meeting_date="October 20, 2026",
        video_id="uHFuCfd5MVQ",
        claims=CIVICCLERK.claims,
    )
    assert video_link(recap, "1:02:03") == "https://www.youtube.com/watch?v=uHFuCfd5MVQ&t=3723s"


def test_an_episode_video_url_is_the_real_media_not_a_fake_youtube_id() -> None:
    data = {
        "video_id": "civicclerk-superiorco-43",
        "source": {"platform": "direct", "media_url": "https://cpmedia.example/abc.mp4"},
    }
    assert _video_url(data) == "https://cpmedia.example/abc.mp4"


def test_an_old_civicclerk_episode_gets_no_youtube_url() -> None:
    assert _video_url({"video_id": "civicclerk-superiorco-43"}) is None
