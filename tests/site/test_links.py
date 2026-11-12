"""Link check: every internal link in a built site resolves to a file (and every fragment exists)."""

from __future__ import annotations

from pathlib import Path

from site_support import BASE_URL, BASE_URL_SUBPATH

from civiccast_brief.site.linkcheck import check_site


def test_built_fixture_site_has_no_broken_internal_links(built: tuple[Path, object]) -> None:
    out, _ = built
    report = check_site(out, BASE_URL)
    assert report.broken == []
    # The check really ran over the pages, the XML and the links in them.
    assert report.pages == 19
    assert report.xml_files == 2
    assert report.links > 150
    assert report.internal > 100
    assert report.external > 0
    assert report.fragments_checked > 0


def test_subpath_site_resolves_with_its_prefix(built_subpath: Path) -> None:
    report = check_site(built_subpath, BASE_URL_SUBPATH)
    assert report.broken == []
    assert report.internal > 100


def test_a_missing_page_is_reported(built: tuple[Path, object]) -> None:
    out, _ = built
    (out / "about" / "index.html").unlink()
    report = check_site(out, BASE_URL)
    assert any("/about/" in problem for problem in report.broken)


def test_a_missing_fragment_is_reported(built: tuple[Path, object]) -> None:
    out, _ = built
    page = out / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</main>", '<p><a href="/search/#no-such-id">jump</a></p></main>', 1
        ),
        encoding="utf-8",
    )
    report = check_site(out, BASE_URL)
    assert any("no #no-such-id" in problem for problem in report.broken)


def test_a_link_to_a_file_that_is_not_in_the_site_is_reported(built: tuple[Path, object]) -> None:
    out, _ = built
    page = out / "search" / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</main>", '<p><a href="/media/missing.mp3">audio</a></p></main>', 1
        ),
        encoding="utf-8",
    )
    report = check_site(out, BASE_URL)
    assert any("/media/missing.mp3" in problem for problem in report.broken)


def test_a_root_relative_link_outside_the_sub_path_is_reported(built_subpath: Path) -> None:
    page = built_subpath / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</main>", '<p><a href="/search/">search</a></p></main>', 1
        ),
        encoding="utf-8",
    )
    report = check_site(built_subpath, BASE_URL_SUBPATH)
    assert any("outside the site base path" in problem for problem in report.broken)
