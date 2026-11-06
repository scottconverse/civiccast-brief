"""Constants shared by the site tests. Imported by name; tests/site is not a package."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "episodes"
BASE_URL = "https://news.example.org"
BASE_URL_SUBPATH = "https://example.org/brief"
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
