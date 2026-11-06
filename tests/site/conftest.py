"""Shared fixtures for the site builder tests. Every test runs offline on invented data."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from site_support import BASE_URL, BASE_URL_SUBPATH, FIXTURES, NOW

from civiccast_brief.site.build import BuildReport, build_site


@pytest.fixture
def episodes_dir(tmp_path: Path) -> Path:
    """A writable copy of the fixture episodes, so a test can change or break files."""
    target = tmp_path / "episodes"
    shutil.copytree(FIXTURES, target)
    return target


@pytest.fixture
def built(tmp_path: Path) -> tuple[Path, BuildReport]:
    """The fixture site built at the domain root."""
    out = tmp_path / "site"
    report = build_site(FIXTURES, out, BASE_URL, now=NOW)
    return out, report


@pytest.fixture
def built_subpath(tmp_path: Path) -> Path:
    """The fixture site built under a sub-path, to check that links keep the path prefix."""
    out = tmp_path / "site-sub"
    build_site(FIXTURES, out, BASE_URL_SUBPATH, now=NOW)
    return out
