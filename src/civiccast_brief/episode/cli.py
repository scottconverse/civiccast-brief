"""Command line: ``python -m civiccast_brief.episode stage|assemble``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from civiccast_brief.episode.build import EpisodeError, assemble, stage
from civiccast_brief.review.store import ReviewError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief.episode")
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("stage", help="put a meeting run on the editor desk")
    s.add_argument("--db", type=Path, required=True, help="review database")
    s.add_argument("--meeting", type=Path, required=True, help="meeting run folder")
    s.add_argument("--script", type=Path, required=True, help="checked script.json")
    s.add_argument("--city", type=Path, help="city folder (desk.json auto-publish)")
    a = commands.add_parser("assemble", help="write one site episode JSON from an approved item")
    a.add_argument("--item", type=Path, required=True, help="exported item folder")
    a.add_argument("--meeting", type=Path, required=True, help="meeting run folder")
    a.add_argument("--script", type=Path, required=True, help="the script.json that was staged")
    a.add_argument("--audio", type=Path, required=True, help="audio folder (episode.mp3 ...)")
    a.add_argument("--media-base-url", required=True, help="where the MP3s will be served")
    a.add_argument("--city", required=True, help="city slug, e.g. longmont")
    a.add_argument("--published", required=True, help="publish time, ISO 8601 with offset")
    a.add_argument("--out", type=Path, required=True, help="episode JSON file to write")
    a.add_argument("--article", type=Path, help="exported, published article item folder")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "stage":
            print(f"staged item {stage(args.db, args.meeting, args.script, args.city)}")
            return 0
        episode = assemble(
            item_dir=args.item,
            meeting_dir=args.meeting,
            script_json=args.script,
            audio_dir=args.audio,
            media_base_url=args.media_base_url,
            city_slug=args.city,
            published=args.published,
            article_dir=args.article,
        )
    except (EpisodeError, ReviewError, OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(episode, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({episode['id']})")
    return 0
