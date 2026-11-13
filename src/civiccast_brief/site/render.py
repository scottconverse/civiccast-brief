"""Jinja2 environment for the site templates, with the filters and globals they use."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from civiccast_brief.site.urls import Urls

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def date_long(value: date | datetime) -> str:
    """October 20, 2026. Written out by hand because %-d is not portable to Windows."""
    return f"{value:%B} {value.day}, {value.year}"


def file_size(num_bytes: int) -> str:
    """Decimal megabytes, the way podcast apps show them: 5.2 MB."""
    if num_bytes >= 1_000_000:
        return f"{num_bytes / 1_000_000:.1f} MB"
    return f"{num_bytes / 1_000:.0f} KB"


@cache
def _static_version(rel: str) -> str:
    return hashlib.sha256((STATIC_DIR / rel).read_bytes()).hexdigest()[:10]


def asset_href(urls: Urls, path: str) -> str:
    """Link to a file under /assets/ with its content hash as ?v=, so a browser that cached an
    older copy (hosts can keep assets for hours) loads the new one after every deploy."""
    return f"{urls.href(path)}?v={_static_version(path.removeprefix('/assets/'))}"


def make_environment(urls: Urls) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals["href"] = urls.href
    env.globals["asset"] = lambda path: asset_href(urls, path)
    return env


def render(env: Environment, template: str, context: dict[str, Any]) -> str:
    return env.get_template(template).render(**context)
