"""Sitemap, robots.txt and JSON-LD: every page is listed, and every structured-data block parses."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from site_support import BASE_URL

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _html_pages(out: Path) -> set[str]:
    """Every HTML page as its site URL, for example https://news.example.org/search/."""
    pages = set()
    for file in out.rglob("index.html"):
        rel = file.parent.relative_to(out).as_posix()
        pages.add(f"{BASE_URL}/" if rel == "." else f"{BASE_URL}/{rel}/")
    return pages


def test_sitemap_lists_every_page_and_nothing_else(built: tuple[Path, object]) -> None:
    out, _ = built
    root = ET.parse(out / "sitemap.xml").getroot()
    listed = {loc.text for loc in root.findall("sm:url/sm:loc", SITEMAP_NS)}
    assert listed == _html_pages(out)
    assert len(listed) == 8


def test_sitemap_entries_are_all_absolute_urls(built: tuple[Path, object]) -> None:
    out, _ = built
    root = ET.parse(out / "sitemap.xml").getroot()
    for loc in root.findall("sm:url/sm:loc", SITEMAP_NS):
        assert loc.text is not None and loc.text.startswith(BASE_URL + "/")


def test_robots_points_at_the_sitemap(built: tuple[Path, object]) -> None:
    out, _ = built
    robots = (out / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert f"Sitemap: {BASE_URL}/sitemap.xml" in robots


def _json_ld_blocks(html: str) -> list[dict]:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    return [json.loads(block) for block in blocks]


def test_json_ld_parses_and_has_the_expected_types(built: tuple[Path, object]) -> None:
    out, _ = built
    home = _json_ld_blocks((out / "index.html").read_text(encoding="utf-8"))
    assert [block["@type"] for block in home] == ["PodcastSeries"]
    assert home[0]["webFeed"] == f"{BASE_URL}/feed.xml"

    episode = _json_ld_blocks(
        (out / "episodes/2026-10-20-example-council/index.html").read_text(encoding="utf-8")
    )
    types = [block["@type"] for block in episode]
    assert types == ["NewsArticle", "PodcastEpisode"]
    news = episode[0]
    assert news["headline"] == "Council approves a test annexation and two ordinances"
    assert news["datePublished"] == "2026-10-21T09:00:00-06:00"
    assert news["url"] == f"{BASE_URL}/episodes/2026-10-20-example-council/"
    podcast = episode[1]
    assert podcast["associatedMedia"]["contentUrl"] == (
        "https://media.example.com/example-council.mp3"
    )
    assert podcast["duration"] == "PT1M15S"
    assert podcast["partOfSeries"]["@type"] == "PodcastSeries"


def test_every_page_json_ld_parses(built: tuple[Path, object]) -> None:
    out, _ = built
    for file in out.rglob("*.html"):
        for block in _json_ld_blocks(file.read_text(encoding="utf-8")):
            assert "@context" in block, file


def test_open_graph_and_share_image_are_set(built: tuple[Path, object]) -> None:
    out, _ = built
    html = (out / "episodes/2026-10-20-example-council/index.html").read_text(encoding="utf-8")
    assert '<meta property="og:type" content="article">' in html
    assert f'<meta property="og:image" content="{BASE_URL}/assets/share-example.png">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    share = out / "assets" / "share-example.png"
    assert share.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
