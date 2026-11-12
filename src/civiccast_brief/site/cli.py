"""Command line for the site builder.

``python -m civiccast_brief.site build`` writes one city's site. ``python -m civiccast_brief.site home``
writes the civiccastbrief.org home page that lists the live cities.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from civiccast_brief.site.build import build_site
from civiccast_brief.site.home import DEFAULT_CITIES, build_home
from civiccast_brief.site.model import InputError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m civiccast_brief.site",
        description="Build the CivicCast Brief static site and podcast feed from episode JSON files.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="write the site into an output folder")
    build.add_argument(
        "--episodes", required=True, type=Path, help="folder with city.json and episode JSON files"
    )
    build.add_argument(
        "--out", required=True, type=Path, help="output folder (empty, or from an earlier build)"
    )
    build.add_argument(
        "--base-url",
        required=True,
        help="public address of the site, such as https://longmont.civiccastbrief.org",
    )
    home = commands.add_parser("home", help="write the civiccastbrief.org home page")
    home.add_argument(
        "--cities",
        type=Path,
        default=DEFAULT_CITIES,
        help='JSON list of cities; only those with "live": true are shown (default: the packaged list)',
    )
    home.add_argument(
        "--out", required=True, type=Path, help="output folder (empty, or from an earlier build)"
    )
    home.add_argument(
        "--base-url", default="https://civiccastbrief.org", help="public address of the home page"
    )
    return parser


def _home(args: argparse.Namespace) -> int:
    try:
        report = build_home(args.cities, args.out, args.base_url)
    except InputError as exc:
        for problem in exc.problems:
            print(f"input error: {problem}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    listed = ", ".join(report.cities) or "none"
    print(f"built the home page into {report.out_dir}; live cities: {listed}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "home":
        return _home(args)
    if args.command != "build":
        return 2
    try:
        report = build_site(args.episodes, args.out, args.base_url)
    except InputError as exc:
        for problem in exc.problems:
            print(f"input error: {problem}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"built {report.episodes} approved episode(s) into {report.out_dir}")
    for episode_id, reason in report.excluded:
        print(f"left out {episode_id}: {reason}")
    print(
        f"pages: {len(report.pages)}, feed items: {report.feed_items}, files: {report.files_written}"
    )
    return 0
