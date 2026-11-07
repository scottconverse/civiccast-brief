"""Pages: each page renders the right parts, with the right links, and escapes what it is given."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from site_support import BASE_URL, FIXTURES, NOW

from civiccast_brief.site.build import build_site
from civiccast_brief.site.model import load_site_input

COUNCIL = "episodes/2026-10-20-example-council/index.html"
SAMPLE = "episodes/2026-10-06-example-sample/index.html"
TRANSCRIPT = "episodes/2026-10-20-example-council/transcript/index.html"


def _text(out: Path, rel: str) -> str:
    return (out / rel).read_text(encoding="utf-8")


def test_home_lists_the_latest_episode_first(built: tuple[Path, object]) -> None:
    out, _ = built
    home = _text(out, "index.html")
    assert home.index("Council approves a test annexation") < home.index(
        "SAMPLE: Planning commission"
    )
    assert "Latest episodes" in home
    assert "Earlier episodes" in home
    assert 'type="application/ld+json"' in home
    assert '"@type": "PodcastSeries"' in home


def test_episode_page_has_player_recap_chapters_and_transcript(
    built: tuple[Path, object],
) -> None:
    out, _ = built
    page = _text(out, COUNCIL)
    assert "<h1" in page and page.count("<h1") == 1
    assert '<audio class="player__audio" controls preload="metadata"' in page
    assert "data-player" in page
    assert 'src="https://media.example.com/example-council.mp3"' in page
    # Recap: each claim cites its timestamp, linked to the video at that second.
    assert 'href="https://www.youtube.com/watch?v=EXAMPLE0001&amp;t=1680s"' in page
    assert "Video at 0:28:00" in page
    assert "<blockquote>That carries unanimously.</blockquote>" in page
    # Chapters link to the audio at that time and are wired to the player.
    assert 'href="https://media.example.com/example-council.mp3#t=40"' in page
    assert 'data-start="40"' in page
    # The episode transcript is the script with speaker names, and links the VTT.
    assert "Maya:" in page
    assert "Timed transcript (WebVTT)" in page
    # The meeting transcript link appears because the episode has one.
    assert "/episodes/2026-10-20-example-council/transcript/" in page
    # The correction is shown on the episode.
    assert "The recorded vote was 6-1." in page


def test_sample_episode_is_marked_and_has_no_recap_or_transcript(
    built: tuple[Path, object],
) -> None:
    out, _ = built
    page = _text(out, SAMPLE)
    assert "notice--sample" in page
    assert "This is a test render" in page
    assert "There is no recap for this episode." in page
    assert "meeting-transcript" not in page
    assert "/transcript/" not in page
    assert not (out / "episodes/2026-10-06-example-sample/transcript").exists()


def test_transcript_lines_link_to_the_video_and_name_unknown_speakers(
    built: tuple[Path, object],
) -> None:
    out, _ = built
    page = _text(out, TRANSCRIPT)
    assert page.count('class="tline"') == 3
    assert 'href="https://www.youtube.com/watch?v=EXAMPLE0001&amp;t=5s"' in page
    assert "Unknown speaker" in page
    assert '<span class="tline__speaker">Mayor</span>' in page
    assert 'href="https://www.youtube.com/watch?v=EXAMPLE0001&amp;t=1680s"' in page


def test_search_page_uses_the_index_and_no_server(built: tuple[Path, object]) -> None:
    out, _ = built
    page = _text(out, "search/index.html")
    assert 'data-index="/search-index.json"' in page
    assert "/assets/search.js" in page
    assert "<noscript>" in page
    index = json.loads(_text(out, "search-index.json"))
    assert {record["id"] for record in index} == {
        "2026-10-20-example-council",
        "2026-10-06-example-sample",
    }
    council = next(record for record in index if record["id"] == "2026-10-20-example-council")
    assert "The council approved the annexation resolution" in council["text"]
    assert "transcript" not in council


def test_corrections_how_made_and_about_pages(built: tuple[Path, object]) -> None:
    out, _ = built
    corrections = _text(out, "corrections/index.html")
    assert "The recorded vote was 6-1." in corrections
    assert "October 23, 2026" in corrections
    how = _text(out, "how-this-is-made/index.html")
    assert "AI-generated from a public meeting video" in how
    assert "Script check." in how
    assert "This episode is AI-generated" in how
    about = _text(out, "about/index.html")
    assert "This site uses no analytics" in about
    assert "not run by or affiliated with the City of Example City" in about
    assert "corrections@example.com" in about


def test_no_tracking_and_only_our_scripts(built: tuple[Path, object]) -> None:
    out, _ = built
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b([^>]*)>", html)
        for attributes in scripts:
            if "application/ld+json" in attributes:
                continue
            assert 'src="/assets/' in attributes, f"{page}: unexpected script {attributes}"
        lowered = html.lower()
        for marker in (
            "google-analytics",
            "googletagmanager",
            "gtag(",
            "plausible",
            "fonts.googleapis",
        ):
            assert marker not in lowered, f"{page}: {marker}"


def test_every_page_has_csp_and_metadata(built: tuple[Path, object]) -> None:
    out, _ = built
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert '<meta http-equiv="Content-Security-Policy"' in html, page
        assert "script-src 'self'" in html, page
        assert 'rel="canonical"' in html, page
        assert 'property="og:image"' in html, page
        assert 'lang="en"' in html, page


def test_user_text_is_escaped(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "ep-2026-10-06-example-sample.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["headline"] = 'Tricky <script>alert("x")</script> & "quotes"'
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    page = _text(out, SAMPLE)
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert" in page
    # The JSON-LD block must not be able to close its own tag.
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page):
        assert "</" not in block
        json.loads(block)


def test_city_settings_are_applied_to_the_footer_and_feed(built: tuple[Path, object]) -> None:
    out, _ = built
    footer_html = _text(out, "index.html").split("<footer", 1)[1]
    assert "CivicCast Brief: Example City is published by CivicCast Brief" in footer_html
    site = load_site_input(FIXTURES)
    assert site.city.name == "Example City"


def test_body_class_never_picks_up_a_layout_rule(built: tuple[Path, object]) -> None:
    # The body carries the nav name ("home", "search"...). A CSS rule for the same class name
    # made the body a two-column grid and squeezed the home episode list to 0 px wide.
    from civiccast_brief.site.build import STATIC_DIR

    out, _ = built
    css = (STATIC_DIR / "site.css").read_text(encoding="utf-8")
    for file in out.rglob("*.html"):
        match = re.search(r'<body class="([^"]*)"', file.read_text(encoding="utf-8"))
        assert match, file
        for name in match.group(1).split():
            assert not re.search(rf"\.{re.escape(name)}(?![\w-])", css), (file, name)
