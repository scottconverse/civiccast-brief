"""The civiccastbrief.org home page: live cities only, logo A, analytics, and the headers rule."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from civiccast_brief.site.build import MARKER
from civiccast_brief.site.home import DEFAULT_CITIES, build_home, load_cities
from civiccast_brief.site.model import InputError

BASE = "https://civiccastbrief.org"
BEACON = (
    '<script defer src="https://static.cloudflareinsights.com/beacon.min.js"'
    """ data-cf-beacon='{"token": "584d3422251846a3a68db1030f40f942"}'></script>"""
)


def _cities(tmp_path: Path, cities: object) -> Path:
    path = tmp_path / "cities.json"
    path.write_text(json.dumps(cities), encoding="utf-8")
    return path


LONGMONT = {
    "name": "Longmont",
    "state": "Colorado",
    "url": "https://longmont.civiccastbrief.org/",
    "bodies": "City Council",
    "note": "5 meetings",
    "live": True,
}
OAKLAND = {
    **LONGMONT,
    "name": "Oakland",
    "state": "California",
    "url": "https://x.org/",
    "live": False,
}


def test_only_live_cities_are_listed(tmp_path: Path) -> None:
    out = tmp_path / "home"
    report = build_home(_cities(tmp_path, [LONGMONT, OAKLAND]), out, BASE)
    assert report.cities == ("Longmont",)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '<a href="https://longmont.civiccastbrief.org/">Longmont</a>' in html
    assert "Oakland" not in html
    assert "City Council · 5 meetings" in html


def test_the_shipped_city_list_has_longmont_and_no_oakland(tmp_path: Path) -> None:
    cities = load_cities(DEFAULT_CITIES)
    live = [city.name for city in cities if city.live]
    assert live == ["Longmont"]
    assert all(city.name != "Oakland" for city in cities)


def test_page_has_logo_beacon_csp_and_assets(tmp_path: Path) -> None:
    out = tmp_path / "home"
    build_home(_cities(tmp_path, [LONGMONT]), out, BASE)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert html.count("<h1") == 1
    assert '<rect class="logo-tile" width="64" height="64" rx="14"/>' in html
    assert "CivicCast <b>Brief</b>" in html
    assert html.count(BEACON) == 1
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert csp
    assert "script-src 'self' https://static.cloudflareinsights.com;" in csp.group(1)
    assert "connect-src 'self' https://cloudflareinsights.com;" in csp.group(1)
    assert '<link rel="canonical" href="https://civiccastbrief.org/">' in html
    assert "Cloudflare Web Analytics, which counts visits without cookies" in html
    for rel in ("assets/site.css", "assets/favicon.svg", "assets/favicon.png", "assets/share.png"):
        assert (out / rel).is_file(), rel
    assert (out / MARKER).is_file()


def test_headers_keep_no_transform_and_share_the_fonts(tmp_path: Path) -> None:
    out = tmp_path / "home"
    build_home(_cities(tmp_path, [LONGMONT]), out, BASE)
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert headers.startswith("/*\n")
    assert "no-transform" in headers
    assert "/assets/fonts/*\n  Access-Control-Allow-Origin: *\n" in headers


def test_no_live_city_says_so(tmp_path: Path) -> None:
    out = tmp_path / "home"
    build_home(_cities(tmp_path, [OAKLAND]), out, BASE)
    assert "No cities are live yet." in (out / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("cities", "message"),
    [
        ({"name": "x"}, "must be a JSON list"),
        (["Longmont"], "must be an object"),
        ([{**LONGMONT, "name": ""}], "name must be a non-empty string"),
        ([{**LONGMONT, "url": "http://longmont.example.org/"}], "url must be an https://"),
        ([{**LONGMONT, "live": "yes"}], "live must be true or false"),
    ],
)
def test_bad_city_files_are_refused(tmp_path: Path, cities: object, message: str) -> None:
    with pytest.raises(InputError) as caught:
        load_cities(_cities(tmp_path, cities))
    assert any(message in problem for problem in caught.value.problems), caught.value.problems


def test_unreadable_city_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cities.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError, match="cannot read"):
        load_cities(path)


def test_command_line_builds_the_home_page(tmp_path: Path) -> None:
    out = tmp_path / "home"
    result = subprocess.run(
        [sys.executable, "-m", "civiccast_brief.site", "home", "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "live cities: Longmont" in result.stdout
    assert "Longmont" in (out / "index.html").read_text(encoding="utf-8")


def test_command_line_reports_bad_input(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "civiccast_brief.site",
            "home",
            "--cities",
            str(_cities(tmp_path, [{**LONGMONT, "url": "ftp://x"}])),
            "--out",
            str(tmp_path / "home"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "url must be an https://" in result.stderr + result.stdout
