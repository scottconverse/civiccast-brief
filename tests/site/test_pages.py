"""Pages: each page renders the right parts, with the right links, and escapes what it is given."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from site_support import BASE_URL, FIXTURES, NOW

from civiccast_brief.site.build import build_site
from civiccast_brief.site.model import InputError, load_site_input

COUNCIL = "episodes/2026-10-20-example-council/index.html"
SAMPLE = "episodes/2026-10-06-example-sample/index.html"
TRANSCRIPT = "episodes/2026-10-20-example-council/transcript/index.html"


def _text(out: Path, rel: str) -> str:
    return (out / rel).read_text(encoding="utf-8")


def test_home_lists_the_latest_episode_first(built: tuple[Path, object]) -> None:
    out, _ = built
    home = _text(out, "index.html")
    # The article headline replaces the episode headline once a story is written.
    assert home.index("Example City approves an annexation on a 6-1 vote") < home.index(
        "SAMPLE: Planning commission"
    )
    assert 'id="lead-title"' in home
    assert "Also recently" in home
    assert "Every meeting" in home
    # The lead story's first vote is split (6-1), so it gets the split badge.
    assert '<span class="vote split">6-1</span>' in home
    # Docket rows sit under a month header that links to that month's archive.
    assert '<a href="/archive/2026-10/">October 2026</a>' in home
    assert '<a class="chip" href="/topics/land-use/">Land use</a>' in home
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
    # Story: each cited paragraph links to the video at the claim's second, shown as a timecode.
    assert 'class="cite" href="https://www.youtube.com/watch?v=EXAMPLE0001&amp;t=5502s"' in page
    assert ">1:31:42<span" in page
    assert '<p class="t">The Example City Council approved the annexation resolution' in page
    assert "<h2>Public comment</h2>" in page
    assert '<span class="vote split">6-1 · Passed</span>' in page
    assert '<span class="vote pass">Passed unanimously</span>' in page
    assert "<blockquote>We need more study of the traffic on Test Road.</blockquote>" in page
    # The checked claims stay on the page, folded under the story.
    assert '<summary id="recap-title">The checked facts this story is built from (4)' in page
    assert 'href="https://www.youtube.com/watch?v=EXAMPLE0001&amp;t=1680s"' in page
    assert "<blockquote>That carries unanimously.</blockquote>" in page
    # Chapters link to the audio at that time and are wired to the player.
    assert 'href="https://media.example.com/example-council.mp3#t=40"' in page
    assert 'data-start="40"' in page
    # The episode transcript is the script with speaker names, and links the VTT.
    assert "Maya:" in page
    assert "Timed transcript (WebVTT)" in page
    # The meeting transcript link appears because the episode has one.
    assert "/episodes/2026-10-20-example-council/transcript/" in page
    # Republish notice and AI disclaimer on every story; corrections are gone.
    assert "Free to republish." in page
    assert "Written with AI." in page
    assert "orrection" not in page
    assert 'href="/boards/city-council/"' in page
    assert 'href="/topics/land-use/"' in page


def test_sample_episode_is_marked_and_has_no_recap_or_transcript(
    built: tuple[Path, object],
) -> None:
    out, _ = built
    page = _text(out, SAMPLE)
    assert "<strong>SAMPLE.</strong>" in page
    assert "This is a test render" in page
    assert "There is no recap for this episode." in page
    assert "Free to republish." in page
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


def test_how_made_and_about_pages_and_no_corrections_page(built: tuple[Path, object]) -> None:
    out, _ = built
    assert not (out / "corrections").exists()
    for file in out.rglob("*.html"):
        assert "/corrections/" not in file.read_text(encoding="utf-8"), file
    how = _text(out, "how-this-is-made/index.html")
    assert "AI-generated from a public meeting video" in how
    assert "Script check." in how
    assert "This episode is AI-generated" in how
    about = _text(out, "about/index.html")
    assert "This site uses Cloudflare Web Analytics to count visits. It sets no cookies" in about
    assert 'id="privacy"' in about
    assert 'id="republish"' in about
    assert "not run by or affiliated with the City of Example City" in about
    assert "corrections@example.com" in about


BEACON = (
    ' defer src="https://static.cloudflareinsights.com/beacon.min.js"'
    """ data-cf-beacon='{"token": "584d3422251846a3a68db1030f40f942"}'"""
)


def test_only_our_scripts_and_the_cloudflare_beacon(built: tuple[Path, object]) -> None:
    out, _ = built
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b([^>]*)>", html)
        # Exactly one analytics script per page: Cloudflare's beacon with our token.
        assert [a for a in scripts if "cloudflareinsights" in a] == [BEACON], page
        for attributes in scripts:
            if "application/ld+json" in attributes or attributes == BEACON:
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
        assert "script-src 'self' https://static.cloudflareinsights.com;" in html, page
        assert "connect-src 'self' https://cloudflareinsights.com;" in html, page
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
    assert "This site uses Cloudflare Web Analytics, which counts visits without cookies." in (
        footer_html
    )
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


def test_headers_file_stops_host_script_injection(built: tuple[Path, object]) -> None:
    out, _ = built
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert headers.startswith("/*\n")
    assert "no-transform" in headers


def test_board_topic_archive_and_podcast_pages(built: tuple[Path, object]) -> None:
    out, _ = built
    boards = _text(out, "boards/index.html")
    assert '<a href="/boards/city-council/">City Council</a>' in boards
    assert '<a href="/boards/planning-commission/">Planning Commission</a>' in boards
    assert "Example City approves an annexation" in _text(out, "boards/city-council/index.html")
    topics = _text(out, "topics/index.html")
    assert '<a href="/topics/land-use/">Land use and zoning</a>' in topics
    assert "Example City approves an annexation" in _text(out, "topics/land-use/index.html")
    archive = _text(out, "archive/index.html")
    assert '<a href="/archive/2026-10/">October 2026</a>' in archive
    assert _text(out, "archive/2026-10/index.html").count('class="row"') == 2
    podcast = _text(out, "podcast/index.html")
    assert 'class="btn solid" href="/feed.xml">RSS feed</a>' in podcast
    # city.json has no Apple or Spotify address, so there are no buttons for them.
    assert "Apple Podcasts" not in podcast
    assert "Spotify" not in podcast
    sitemap = _text(out, "sitemap.xml")
    for path in ("/boards/city-council/", "/topics/land-use/", "/archive/2026-10/", "/podcast/"):
        assert f"{BASE_URL}{path}" in sitemap, path


def test_header_search_sends_q_to_the_search_page(built: tuple[Path, object]) -> None:
    out, _ = built
    home = _text(out, "index.html")
    assert '<form class="search" role="search" action="/search/" method="get">' in home
    assert '<input id="site-q" name="q" type="search" placeholder="Search meetings"' in home


def test_apple_and_spotify_buttons_show_only_when_set(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    city_path = episodes / "city.json"
    city = json.loads(city_path.read_text(encoding="utf-8"))
    city["apple_podcasts_url"] = "https://podcasts.apple.com/us/podcast/example/id1234567890"
    city["spotify_url"] = "https://open.spotify.com/show/0123456789abcdefABCDEF"
    city_path.write_text(json.dumps(city), encoding="utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    for rel in ("index.html", "podcast/index.html"):
        page = _text(out, rel)
        assert 'href="https://podcasts.apple.com/us/podcast/example/id1234567890"' in page, rel
        assert 'href="https://open.spotify.com/show/0123456789abcdefABCDEF"' in page, rel


def test_episode_without_article_falls_back_to_the_claim_list(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "ep-2026-10-20-example-council.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["article"]
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    page = _text(out, COUNCIL)
    assert '<h2 id="recap-title">Recap</h2>' in page
    assert "Video at 0:28:00" in page
    assert "The Example City Council approved the annexation resolution" not in page
    assert "<h2>Public comment</h2>" not in page
    assert "Council approves a test annexation" in page
    assert "Free to republish." in page
    assert 'data-start="40"' in page


def test_pages_link_the_logo_favicon_and_fonts(built: tuple[Path, object]) -> None:
    out, _ = built
    home = _text(out, "index.html")
    assert '<rect class="logo-tile" width="64" height="64" rx="14"/>' in home
    assert "CivicCast <b>Brief</b><small>Example City</small>" in home
    assert '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">' in home
    assert (out / "assets/favicon.svg").read_text(encoding="utf-8").startswith("<svg")
    assert (out / "assets/favicon.png").read_bytes().startswith(b"\x89PNG")
    css = (out / "assets/site.css").read_text(encoding="utf-8")
    fonts = re.findall(r'url\("?(fonts/[^")]+)"?\)', css)
    assert len(fonts) >= 4
    for font in fonts:
        assert (out / "assets" / font).is_file(), font


def test_a_paragraph_that_cites_document_pages_links_each_page(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "ep-2026-10-20-example-council.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    doc = (
        "https://portal.example.org/Public/CompiledDocument?meetingTemplateId=1&compileOutputType=1"
    )
    data["article"]["sections"][0]["paragraphs"][0]["pages"] = [
        {"kind": "minutes", "url": doc, "page": 24}
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    page = _text(out, COUNCIL)
    link = doc.replace("&", "&amp;") + "#page=24"
    assert f'<a class="cite" href="{link}" target="_blank" rel="noopener">minutes p. 24' in page
    assert "opens this page of the meeting document in a new tab" in page
    assert ", and from the meeting documents." in page

    data["article"]["sections"][0]["paragraphs"][0]["pages"] = [
        {"kind": "minutes", "url": "javascript:alert(1)", "page": 0}
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError) as caught:
        load_site_input(episodes)
    problems = " ".join(caught.value.problems)
    assert "pages[1].url must be the https address of the document" in problems


def test_the_byline_and_transcript_title_do_not_repeat_the_meeting_date(tmp_path: Path) -> None:
    """A YouTube title ends with the date: "City Council Regular Session - 20 October 2026"."""
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "ep-2026-10-20-example-council.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["meeting_title"] = "City Council Regular Session - 20 October 2026"
    data["chapters"][1]["title"] = "public hearing"
    path.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "site"
    build_site(episodes, out, BASE_URL, now=NOW)
    page = _text(out, COUNCIL)
    assert "video of the City Council Regular Session, October 20, 2026." in page
    assert "20 October 2026" not in page
    assert "Public hearing" in page and "public hearing<" not in page
    transcript = _text(out, TRANSCRIPT)
    assert "Transcript: City Council Regular Session</h1>" in transcript
    assert "20 October 2026" not in transcript
