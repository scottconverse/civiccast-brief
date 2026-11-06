"""Command line for the site builder: ``python -m civiccast_brief.site build``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from civiccast_brief.site.build import build_site
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
        help="public address of the site, such as https://longmont.civiccast.org",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
