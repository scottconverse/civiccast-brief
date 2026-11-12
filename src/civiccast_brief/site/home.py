"""The CivicCast Brief home page (civiccastbrief.org): logo A and a card for each live city.

Input is a JSON file with a list of cities. Only cities with "live": true are listed, so a city
that is still being set up (or one that was dropped) never shows on the page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from civiccast_brief.site import build, images
from civiccast_brief.site.model import InputError
from civiccast_brief.site.pages import (
    ANALYTICS_CONNECT_ORIGIN,
    ANALYTICS_SCRIPT,
    ANALYTICS_SCRIPT_ORIGIN,
    ANALYTICS_TOKEN,
    FAVICON_PNG,
    FAVICON_SVG,
)
from civiccast_brief.site.render import make_environment, render
from civiccast_brief.site.urls import Urls

DEFAULT_CITIES = Path(__file__).parent / "home_cities.json"
SHARE_PATH = "/assets/share.png"

#: The sign-up pages on signup.civiccastbrief.org (another origin) use the same font files,
#: so the fonts carry a CORS header.
HOME_HEADERS_FILE = build.HEADERS_FILE + "/assets/fonts/*\n  Access-Control-Allow-Origin: *\n"


@dataclass(frozen=True)
class HomeCity:
    name: str
    state: str
    url: str
    bodies: str
    note: str
    live: bool


@dataclass(frozen=True)
class HomeReport:
    out_dir: Path
    cities: tuple[str, ...]
    files_written: int


def load_cities(path: Path) -> tuple[HomeCity, ...]:
    """Read and check the cities file. Raises InputError with every problem found."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError([f"{path}: cannot read: {exc}"]) from exc
    if not isinstance(data, list):
        raise InputError([f"{path}: must be a JSON list of cities"])
    problems: list[str] = []
    cities: list[HomeCity] = []
    for number, item in enumerate(data, start=1):
        where = f"{path.name} city {number}"
        if not isinstance(item, dict):
            problems.append(f"{where}: must be an object")
            continue
        texts: dict[str, str] = {}
        for key in ("name", "state", "url", "bodies", "note"):
            value = item.get(key, "")
            required = key in ("name", "state", "url")
            if not isinstance(value, str) or (required and not value.strip()):
                problems.append(f"{where}: {key} must be a non-empty string")
                value = ""
            texts[key] = value.strip()
        live = item.get("live", False)
        if not isinstance(live, bool):
            problems.append(f"{where}: live must be true or false")
            live = False
        parsed = urlparse(texts["url"])
        if texts["url"] and (parsed.scheme != "https" or not parsed.netloc):
            problems.append(f"{where}: url must be an https:// address")
        cities.append(HomeCity(live=live, **texts))
    if problems:
        raise InputError(problems)
    return tuple(cities)


def home_context(cities: tuple[HomeCity, ...], urls: Urls) -> dict[str, Any]:
    return {
        "cities": [city for city in cities if city.live],
        "not_found": False,
        "canonical": urls.absolute("/"),
        "og_image": urls.absolute(SHARE_PATH),
        "favicon_svg": urls.href(FAVICON_SVG),
        "favicon_png": urls.href(FAVICON_PNG),
        "analytics": {
            "token": ANALYTICS_TOKEN,
            "script": ANALYTICS_SCRIPT,
            "script_origin": ANALYTICS_SCRIPT_ORIGIN,
            "connect_origin": ANALYTICS_CONNECT_ORIGIN,
        },
    }


def build_home(cities_file: Path, out_dir: Path, base_url: str) -> HomeReport:
    """Write the home page into out_dir (empty, or from an earlier build)."""
    urls = Urls.from_base(base_url)
    cities = load_cities(cities_file)
    context = home_context(cities, urls)
    build.prepare_output(out_dir)
    files = build.write_static(out_dir)
    build.write_file(out_dir, SHARE_PATH, images.share_png())
    env = make_environment(urls)
    build.write_file(out_dir, "index.html", render(env, "landing.html", context))
    # Cloudflare Pages answers an unknown address with 404.html and a 404 status; without it,
    # Pages serves the home page with a 200.
    missing = {**context, "not_found": True}
    build.write_file(out_dir, "404.html", render(env, "landing.html", missing))
    build.write_file(out_dir, build.HEADERS_PATH, HOME_HEADERS_FILE)
    return HomeReport(
        out_dir=out_dir,
        cities=tuple(city.name for city in context["cities"]),
        files_written=files + 4,
    )
