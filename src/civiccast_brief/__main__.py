"""Top-level commands.

    python -m civiccast_brief setup DIR --slug SLUG --name NAME --state STATE
        --contact-email ADDRESS --podcast-email ADDRESS
        [--body "City Council"] [--roster-page URL] [--granicus HOST --view-id N]

    python -m civiccast_brief tools [--test-video URL]

``setup`` starts a city folder (see civiccast_brief/city.py and docs/set-up-a-city.md).
``tools`` installs or updates yt-dlp in CivicCast Brief's own folder (see civiccast_brief/tools.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from civiccast_brief.city import CityError, setup


def _setup(args: argparse.Namespace) -> int:
    try:
        written = setup(
            args.folder,
            slug=args.slug,
            name=args.name,
            state=args.state,
            body=args.body,
            contact_email=args.contact_email,
            podcast_email=args.podcast_email,
            roster_page=args.roster_page,
            granicus_host=args.granicus,
            granicus_view_id=args.view_id,
        )
    except CityError as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    print(
        f"next: add the members to {args.folder / 'roster.json'} from the city's own page, "
        "then run one meeting (see docs/set-up-a-city.md)"
    )
    return 0


def _tools(args: argparse.Namespace) -> int:
    from civiccast_brief import tools

    try:
        outcome = tools.install_ytdlp(tools.test_video(args.test_video))
    except Exception as exc:  # noqa: BLE001 - every failure is told to the user in one line
        print(f"tools failed: {exc}", file=sys.stderr)
        return 1
    print(outcome.reason)
    if outcome.active:
        print(f"yt-dlp {outcome.active} is active")
    return 1 if outcome.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("setup", help="start a city folder: city.json and roster.json")
    cmd.add_argument("folder", type=Path, help="the new city folder, e.g. cities/oakland")
    cmd.add_argument("--slug", required=True, help="short web name, e.g. oakland")
    cmd.add_argument("--name", required=True, help="the city's name, e.g. Oakland")
    cmd.add_argument("--state", required=True, help="the state's name, e.g. California")
    cmd.add_argument(
        "--body", default="City Council", help="the meeting body (default: %(default)s)"
    )
    cmd.add_argument("--contact-email", required=True, help="where readers send corrections")
    cmd.add_argument("--podcast-email", required=True, help="podcast owner email for the feed")
    cmd.add_argument("--roster-page", default="", help="the city's page that lists the members")
    cmd.add_argument("--granicus", metavar="HOST", help="Granicus host, e.g. oakland.granicus.com")
    cmd.add_argument("--view-id", type=int, help="Granicus ViewPublisher view_id")
    cmd.set_defaults(run=_setup)
    cmd = commands.add_parser("tools", help="install or update yt-dlp in CivicCast Brief's folder")
    cmd.add_argument(
        "--test-video",
        help="a public YouTube meeting video with captions (default: $CIVICCAST_RECORD_TEST_VIDEO)",
    )
    cmd.set_defaults(run=_tools)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
