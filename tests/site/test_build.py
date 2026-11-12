"""Build orchestration: the output folder rules, the command line, and the files a build writes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from site_support import BASE_URL, FIXTURES, NOW

from civiccast_brief.site.build import MARKER, build_site
from civiccast_brief.site.urls import Urls


def test_build_writes_every_expected_file(built: tuple[Path, object]) -> None:
    out, report = built
    expected = [
        "index.html",
        "episodes/2026-10-20-example-council/index.html",
        "episodes/2026-10-20-example-council/transcript/index.html",
        "episodes/2026-10-06-example-sample/index.html",
        "search/index.html",
        "boards/index.html",
        "boards/city-council/index.html",
        "boards/planning-commission/index.html",
        "topics/index.html",
        "topics/land-use/index.html",
        "archive/index.html",
        "archive/2026-10/index.html",
        "podcast/index.html",
        "how-this-is-made/index.html",
        "about/index.html",
        "404.html",
        "search-index.json",
        "feed.xml",
        "sitemap.xml",
        "robots.txt",
        "_headers",
        "assets/site.css",
        "assets/print.css",
        "assets/player.js",
        "assets/search.js",
        "assets/favicon.svg",
        "assets/favicon.png",
        "assets/fonts/public-sans-latin-400-normal.woff2",
        "assets/fonts/LICENSE-public-sans.txt",
        "assets/share-example.png",
        "assets/podcast-art-example.png",
        MARKER,
    ]
    for rel in expected:
        assert (out / rel).is_file(), rel
    assert report.episodes == 2
    assert report.excluded == (("2026-09-15-example-unapproved", "not approved"),)
    assert not (out / "episodes/2026-09-15-example-unapproved").exists()
    assert not (out / "corrections").exists()


def test_rebuild_replaces_a_previous_build_in_place(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site(FIXTURES, out, BASE_URL, now=NOW)
    stale = out / "episodes" / "old-episode" / "index.html"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    build_site(FIXTURES, out, BASE_URL, now=NOW)
    assert not stale.exists()
    assert (out / "index.html").is_file()


def test_refuses_a_folder_it_did_not_make(tmp_path: Path) -> None:
    out = tmp_path / "someone-elses-files"
    out.mkdir()
    (out / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError, match="was not made by this builder"):
        build_site(FIXTURES, out, BASE_URL, now=NOW)
    assert (out / "notes.txt").read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize(
    "base",
    ["example.org", "ftp://example.org", "https://example.org/?utm=1", "https://example.org/#x"],
)
def test_base_url_must_be_a_plain_absolute_address(base: str) -> None:
    with pytest.raises(ValueError):
        Urls.from_base(base)


def test_base_url_keeps_a_sub_path() -> None:
    urls = Urls.from_base("https://example.org/brief/")
    assert urls.base == "https://example.org/brief"
    assert urls.href("/search/") == "/brief/search/"
    assert urls.absolute("/feed.xml") == "https://example.org/brief/feed.xml"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "civiccast_brief.site", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_command_line_builds_the_site(tmp_path: Path) -> None:
    out = tmp_path / "cli-site"
    result = _run_cli(
        "build", "--episodes", str(FIXTURES), "--out", str(out), "--base-url", BASE_URL
    )
    assert result.returncode == 0, result.stderr
    assert "built 2 approved episode(s)" in result.stdout
    assert "left out 2026-09-15-example-unapproved: not approved" in result.stdout
    assert (out / "feed.xml").is_file()


def test_command_line_reports_bad_input_and_exits_nonzero(tmp_path: Path) -> None:
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    result = _run_cli(
        "build", "--episodes", str(episodes), "--out", str(tmp_path / "x"), "--base-url", BASE_URL
    )
    assert result.returncode == 1
    assert "input error" in result.stderr
    assert "city.json is required" in result.stderr


def test_command_line_needs_a_subcommand() -> None:
    result = _run_cli()
    assert result.returncode != 0


def test_an_unknown_address_gets_a_not_found_page_kept_out_of_search(
    built: tuple[Path, object],
) -> None:
    """Cloudflare Pages serves 404.html, with a 404, for an address with no page. Without the
    file it served the front page with a 200 (seen live at /corrections/, 2026-11-11)."""
    out, _ = built
    html = (out / "404.html").read_text(encoding="utf-8")
    assert '<h1 id="notfound-title">Page not found</h1>' in html
    assert '<meta name="robots" content="noindex">' in html
    assert "404.html" not in (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "noindex" not in (out / "index.html").read_text(encoding="utf-8")
