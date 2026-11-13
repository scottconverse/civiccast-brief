"""The footer links to the editor desk's sign-in page when the city sets desk_url."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from site_support import BASE_URL, FIXTURES, NOW

from civiccast_brief.site.build import build_site
from civiccast_brief.site.model import InputError, load_site_input


def _episodes(tmp_path: Path, **extra: object) -> Path:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "city.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")
    return episodes


def _footers(out: Path) -> list[str]:
    pages = list(out.rglob("*.html"))
    assert pages
    return [p.read_text(encoding="utf-8").split('<footer class="s-foot">', 1)[1] for p in pages]


def test_every_footer_links_the_desk_sign_in_page(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site(
        _episodes(tmp_path, desk_url="https://desk.example.org/desk/"), out, BASE_URL, now=NOW
    )
    for footer in _footers(out):
        assert (
            '<a href="https://desk.example.org/desk/sign-in" rel="nofollow">Editor sign-in</a>'
            in footer
        )


def test_desk_url_must_be_https(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="desk_url"):
        load_site_input(_episodes(tmp_path, desk_url="http://desk.example.org/desk"))
