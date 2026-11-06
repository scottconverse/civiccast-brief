"""Jinja2 environment for the site templates, with the filters and globals they use."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from civiccast_brief.site.urls import Urls

TEMPLATE_DIR = Path(__file__).parent / "templates"


def date_long(value: date | datetime) -> str:
    """October 20, 2026. Written out by hand because %-d is not portable to Windows."""
    return f"{value:%B} {value.day}, {value.year}"


def file_size(num_bytes: int) -> str:
    """Decimal megabytes, the way podcast apps show them: 5.2 MB."""
    if num_bytes >= 1_000_000:
        return f"{num_bytes / 1_000_000:.1f} MB"
    return f"{num_bytes / 1_000:.0f} KB"


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
    return env


def render(env: Environment, template: str, context: dict[str, Any]) -> str:
    return env.get_template(template).render(**context)
