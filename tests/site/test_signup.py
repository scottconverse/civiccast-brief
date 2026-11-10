"""Email sign-up form: shown only when the city turns on email_signup, and allowed by the CSP."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from site_support import BASE_URL, FIXTURES, NOW

from civiccast_brief.site.build import build_site
from civiccast_brief.site.model import InputError, load_site_input

SIGNUP = "https://signup.example.org/subscribe"


def _with_signup(tmp_path: Path, value: object, *, email_signup: bool = True) -> Path:
    episodes = tmp_path / "episodes"
    shutil.copytree(FIXTURES, episodes)
    path = episodes / "city.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["signup_url"] = value
    data["email_signup"] = email_signup
    path.write_text(json.dumps(data), encoding="utf-8")
    return episodes


def _csp(html: str) -> str:
    match = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html)
    assert match
    return match.group(1)


def test_no_signup_url_means_no_form_and_no_form_targets(built: tuple[Path, object]) -> None:
    out, _ = built
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert "<form" not in html or 'role="search"' in html, page
        # Only the header search (a GET to our own /search/ page) may submit.
        assert "form-action 'self';" in _csp(html) + ";", page
        assert "By email" not in html, page


def test_signup_form_is_on_every_page_and_csp_allows_only_its_origin(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site(_with_signup(tmp_path, SIGNUP), out, BASE_URL, now=NOW)
    pages = list(out.rglob("*.html"))
    assert pages
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert "form-action 'self' https://signup.example.org;" in _csp(html) + ";", page
        form = html.split('<form class="signup"', 1)[1].split("</form>", 1)[0]
        assert f'action="{SIGNUP}"' in form and 'method="post"' in form
        assert re.search(r'<input[^>]*type="email"[^>]*name="email"[^>]*required', form)
        assert '<label for="signup-email"' in form
        assert 'name="city" value="example"' in form
        # Honeypot for bots: hidden from people and from screen readers.
        assert re.search(r'<input[^>]*name="website"[^>]*tabindex="-1"', form)
        assert 'aria-hidden="true"' in form


def test_signup_url_alone_shows_no_form(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site(_with_signup(tmp_path, SIGNUP, email_signup=False), out, BASE_URL, now=NOW)
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert '<form class="signup"' not in html, page
        assert "signup.example.org" not in _csp(html), page


def test_email_band_on_the_front_page_when_signup_is_on(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site(_with_signup(tmp_path, SIGNUP), out, BASE_URL, now=NOW)
    assert "By email" in (out / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value",
    ["http://signup.example.org/subscribe", "/subscribe", "https://", "ftp://x.org/s", 5],
)
def test_signup_url_must_be_an_absolute_https_address(tmp_path: Path, value: object) -> None:
    with pytest.raises(InputError, match="signup_url"):
        load_site_input(_with_signup(tmp_path, value))
