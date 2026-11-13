"""Stories stand alone: a published story is on the site with or without an episode."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from site_support import BASE_URL, FIXTURES, NOW

from civiccast_brief.site.build import build_site
from civiccast_brief.site.model import load_site_input
from civiccast_brief.site.titles import shorten_roundup_title, split_meeting_label

STORY_ID = "2026-11-03-example-city-council"
HEADLINE = "Council passes the lone story with no audio"


def _episodes(tmp_path: Path, *, story_id: str = STORY_ID, video: str | None = None) -> Path:
    """The fixture episodes plus one story file made from the council episode's article."""
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    source = json.loads((FIXTURES / "ep-2026-10-20-example-council.json").read_text("utf-8"))
    article = dict(source["article"], headline=HEADLINE)
    story = {
        "id": story_id,
        "city": "example",
        "approved": True,
        "approved_by": "editor desk, 2026-11-04T09:00:00-07:00",
        "board": "Planning Commission",
        "meeting_title": "Planning Commission",
        "meeting_date": "2026-11-03",
        "published": "2026-11-04T09:00:00-07:00",
        "recap": source["recap"],
        "video_url": video or source["video_url"],
        "article": article,
    }
    (episodes / "stories").mkdir()
    (episodes / "stories" / f"{story_id}.json").write_text(json.dumps(story), "utf-8")
    return episodes


def _read(out: Path, rel: str) -> str:
    return (out / rel).read_text(encoding="utf-8")


def test_a_story_with_no_episode_has_its_own_page_with_no_player(tmp_path: Path) -> None:
    out = tmp_path / "site"
    report = build_site(_episodes(tmp_path), out, BASE_URL, now=NOW)
    assert report.stories == 1
    page = _read(out, f"stories/{STORY_ID}/index.html")
    assert HEADLINE in page
    assert "<audio" not in page and "data-player" not in page and "Listen" not in page
    assert 'class="cite"' in page  # still cited to the video


def test_a_story_with_no_episode_is_on_the_front_page_lists_search_and_boards(
    tmp_path: Path,
) -> None:
    out = tmp_path / "site"
    build_site(_episodes(tmp_path), out, BASE_URL, now=NOW)
    home = _read(out, "index.html")
    # It is the newest meeting, so it leads, with its board label and no Listen pill.
    lead = home.split('id="lead-title"', 1)[1].split("</article>", 1)[0]
    assert HEADLINE in lead and f'href="/stories/{STORY_ID}/"' in lead
    assert "Listen" not in lead
    assert "Planning Commission" in lead
    assert HEADLINE in _read(out, "boards/planning-commission/index.html")
    assert HEADLINE in _read(out, "archive/2026-11/index.html")
    index = json.loads(_read(out, "search-index.json"))
    record = next(r for r in index if r["id"] == STORY_ID)
    assert record["url"] == f"/stories/{STORY_ID}/" and record["audio"] is False
    assert f"{BASE_URL}/stories/{STORY_ID}/" in _read(out, "sitemap.xml")
    assert STORY_ID not in _read(out, "feed.xml")  # no audio, no podcast item
    assert "1 story" in home  # the Boards box counts it


def test_a_story_whose_meeting_gets_an_episode_redirects_to_the_episode_page(
    tmp_path: Path,
) -> None:
    episodes = _episodes(tmp_path, story_id="2026-10-20-example-council")
    episode_file = episodes / "ep-2026-10-20-example-council.json"
    data = json.loads(episode_file.read_text("utf-8"))
    data.pop("article")
    episode_file.write_text(json.dumps(data), "utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    page = _read(out, "episodes/2026-10-20-example-council/index.html")
    assert HEADLINE in page and "<audio" in page  # the story rides on the episode
    moved = _read(out, "stories/2026-10-20-example-council/index.html")
    assert 'http-equiv="refresh" content="0; url=/episodes/2026-10-20-example-council/"' in moved
    assert (
        "/stories/2026-10-20-example-council/ /episodes/2026-10-20-example-council/ 301"
        in _read(out, "_redirects")
    )
    assert f"{BASE_URL}/stories/2026-10-20-example-council/" not in _read(out, "sitemap.xml")
    assert load_site_input(episodes).stories == ()


def test_a_meeting_still_is_served_from_the_site_never_hot_linked(tmp_path: Path) -> None:
    jpeg = b"\xff\xd8\xff\xe0 still"
    episodes = _episodes(tmp_path, video="https://www.youtube.com/watch?v=Abc_123-xyZ")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW, fetch_still=lambda vid: jpeg)
    home = _read(out, "index.html")
    assert '<img src="/assets/stills/Abc_123-xyZ.jpg"' in home
    assert (out / "assets/stills/Abc_123-xyZ.jpg").read_bytes() == jpeg
    assert "ytimg" not in home and "youtube.com/vi" not in home
    # Kept for the next build, which then needs no download.
    assert (episodes / "stills" / "Abc_123-xyZ.jpg").is_file()


def test_no_still_means_a_plain_colored_block_with_the_board_name(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site(_episodes(tmp_path), out, BASE_URL, now=NOW)
    home = _read(out, "index.html")
    assert re.search(r'<a class="thumb block c\d lead-thumb"', home)
    assert "<img" not in home.split('class="home-grid"', 1)[1]


def test_front_page_has_the_bozeman_blocks_and_only_the_feeds_that_exist(
    tmp_path: Path,
) -> None:
    out = tmp_path / "site"
    build_site(_episodes(tmp_path), out, BASE_URL, now=NOW)
    home = _read(out, "index.html")
    assert "Thursday, November 5, 2026" in home  # top strip
    assert 'class="chip all"' in home and "All topics" in home
    assert "every public meeting, written up" in home
    assert 'id="filter-board"' in home and 'src="/assets/front.js"' in home
    assert "Earlier in" in home
    assert ">The Podcast<" in home and ">Boards<" in home
    assert 'class="btn solid" href="/feed.xml">RSS feed</a>' in home
    assert "Apple Podcasts" not in home and "Spotify" not in home
    assert "By email" not in home and 'class="signup"' not in home


def test_a_roundup_title_that_lists_every_date_is_shown_short() -> None:
    long = (
        "Longmont weekly roundup: September 22, 2026 and October 6, 2026 and "
        "October 20, 2026 and November 3, 2026"
    )
    assert shorten_roundup_title(long) == "Longmont weekly roundup, Sept. 22 – Nov. 3"
    assert shorten_roundup_title("Longmont council passes X") == "Longmont council passes X"


def test_an_already_built_roundup_is_loaded_with_the_short_title(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "ep-2026-10-20-example-council.json"
    data = json.loads(path.read_text("utf-8"))
    data["headline"] = "Example weekly roundup: October 6, 2026 and October 20, 2026"
    data.pop("article")
    path.write_text(json.dumps(data), "utf-8")
    loaded = load_site_input(episodes)
    headlines = [e.headline for e in loaded.episodes]
    assert "Example weekly roundup, Oct. 6 – Oct. 20" in headlines


def test_a_meeting_label_comes_off_the_sentence() -> None:
    assert split_meeting_label("[September 22, 2026 meeting] The vote passed.") == (
        "September 22, 2026",
        "The vote passed.",
    )
    assert split_meeting_label("No label.") == (None, "No label.")


def test_roundup_recap_lines_show_the_meeting_as_a_label(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "ep-2026-10-20-example-council.json"
    data = json.loads(path.read_text("utf-8"))
    data.pop("article")
    data["recap"]["claims"][0]["text"] = "[October 6, 2026 meeting] The annexation passed."
    path.write_text(json.dumps(data), "utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    page = _read(out, "episodes/2026-10-20-example-council/index.html")
    assert '<span class="meeting-tag">October 6, 2026 meeting</span> The annexation passed.' in page
    assert "[October 6, 2026 meeting]" not in page
