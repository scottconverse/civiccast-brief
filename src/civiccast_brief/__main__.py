"""Top-level commands.

    python -m civiccast_brief setup DIR --slug SLUG --name NAME --state STATE
        --contact-email ADDRESS --podcast-email ADDRESS
        [--body "City Council"] [--roster-page URL]
        [--platform youtube|granicus|cablecast|none] [--granicus HOST --view-id N]
        [--cablecast HOST]

    python -m civiccast_brief tools [--test-video URL]

    python -m civiccast_brief evidence (--meeting ID | --video VIDEO_ID) [--db PATH]
        [--check DRAFT.txt] [--out FILE.json]

    python -m civiccast_brief city find --name NAME --state STATE [--website URL]
        [--also "BODY | URL"]... [--out FOLDER] [--dry-run]
    python -m civiccast_brief city check --city FOLDER [--body NAME]... [--captions]

``setup`` starts a city folder (see civiccast_brief/city.py and docs/set-up-a-city.md).
``tools`` installs or updates yt-dlp in CivicCast Brief's own folder (see civiccast_brief/tools.py).
``evidence`` shows what the record holds for one meeting, or checks a draft against it
(see civiccast_brief/record/lookup.py).
``city`` finds a city's meeting sources with no model, and checks them (civiccast_brief/citybuild).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from civiccast_brief.city import CityError, setup

_PLATFORM_LABELS = {
    "youtube": "YouTube",
    "granicus": "Granicus",
    "cablecast": "Cablecast",
    "none": "none of these (no video archive yet)",
}


def _found_platforms(args: argparse.Namespace) -> dict[str, str]:
    """The video platforms the command line and the folder's sources.json already name, each
    with where it was seen. Granicus and Cablecast hosts from sources.json fill in the flags."""
    from civiccast_brief.sources import SourceError, load_sources

    seen: dict[str, str] = {}
    if args.granicus:
        seen["granicus"] = "--granicus"
    if args.cablecast:
        seen["cablecast"] = "--cablecast"
    try:
        sources = load_sources(args.folder)
    except SourceError:
        sources = []
    for source in sources:
        if source.kind == "granicus" and "granicus" not in seen:
            seen["granicus"] = f"{args.folder / 'sources.json'}: {source.host}"
            args.granicus = args.granicus or source.host
            if args.view_id is None and source.view_id.isdigit():
                args.view_id = int(source.view_id)
        elif source.kind == "cablecast" and "cablecast" not in seen:
            seen["cablecast"] = f"{args.folder / 'sources.json'}: {source.host}"
            args.cablecast = args.cablecast or source.host
        elif source.kind == "youtube" and "youtube" not in seen:
            seen["youtube"] = f"{args.folder / 'sources.json'}: channel {source.channel_id}"
    return seen


def _ask_platform(options: list[str], found: dict[str, str]) -> str | None:
    """Ask which platform, by number. None when there is no one to ask (input is not a terminal)."""
    if not sys.stdin.isatty():
        return None
    print("Where does this city keep its meeting videos?")
    for number, name in enumerate(options, start=1):
        where = f"  (found: {found[name]})" if name in found else ""
        print(f"  {number}. {_PLATFORM_LABELS[name]}{where}")
    while True:
        try:
            answer = input(f"Type 1-{len(options)}: ").strip()
        except EOFError:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        print(f"Type a number from 1 to {len(options)}.")


def _choose_platform(args: argparse.Namespace) -> str | None:
    """The video platform: ``--platform`` when given; else the one platform found; else ask.
    Never YouTube by default. None (with a message) when it cannot be settled."""
    found = _found_platforms(args)
    if args.platform:
        return str(args.platform)
    if len(found) == 1:
        (only,) = found
        print(f"Video platform: {_PLATFORM_LABELS[only]} (found: {found[only]}).")
        return only
    options = list(found) if found else list(_PLATFORM_LABELS)
    picked = _ask_platform(options, found)
    if picked is None:
        why = (
            "more than one video platform was found: " + ", ".join(found)
            if found
            else "no video platform was found"
        )
        print(
            f"setup failed: {why}. Say which one with --platform "
            f"({', '.join(_PLATFORM_LABELS)}); it is not guessed.",
            file=sys.stderr,
        )
    return picked


def _setup(args: argparse.Namespace) -> int:
    platform = _choose_platform(args)
    if platform is None:
        return 2
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
            platform=platform,
            cablecast_host=args.cablecast,
        )
    except CityError as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    print(
        f"capture.platform is set to {platform!r} in {args.folder / 'city.json'}. Only the "
        "single-meeting command (`meeting run --city`) uses it; edit city.json to change it."
    )
    print(
        "next, in this order (see docs/set-up-a-city.md):\n"
        "  1. add the city's sources: `city find --out` or the desk's Sources screen, then "
        "`city check`\n"
        f"  2. add the members to {args.folder / 'roster.json'} from the city's own page\n"
        "  3. run one meeting"
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


def _evidence(args: argparse.Namespace) -> int:
    import json

    from civiccast_brief.record import lookup
    from civiccast_brief.record.config import default_db_path

    db = args.db or default_db_path()
    if not db.exists():
        print(f"evidence failed: no record database at {db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        meeting_id = lookup.find_meeting(conn, meeting_id=args.meeting, video_id=args.video)
        captions = lookup.caption_video(conn, meeting_id, args.video)
        pack = lookup.pack_for(conn, meeting_id, video_id=captions.video_id)
        print(captions.note)
        if args.check:
            result = lookup.check(pack, args.check.read_text(encoding="utf-8"))
            print(f"{result['grounded']} of {result['sentences']} sentences stand on the record")
        else:
            result = lookup.describe(conn, meeting_id, pack, captions)
            print(result["summary"])
    except LookupError as exc:
        print(f"evidence failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.out:
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    if args.check:
        return 0 if result["grounded"] == result["sentences"] else 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("setup", help="start a city folder: city.json and roster.json")
    cmd.add_argument("folder", type=Path, help="the new city folder, e.g. cities/oakland")
    cmd.add_argument("--slug", required=True, help="short web name, e.g. oakland")
    cmd.add_argument("--name", required=True, help="the city's name, e.g. Oakland")
    cmd.add_argument(
        "--state",
        required=True,
        help="the state, e.g. California or CA (a code is stored as the full name)",
    )
    cmd.add_argument(
        "--body", default="City Council", help="the meeting body (default: %(default)s)"
    )
    cmd.add_argument("--contact-email", required=True, help="where readers send corrections")
    cmd.add_argument("--podcast-email", required=True, help="podcast owner email for the feed")
    cmd.add_argument("--roster-page", default="", help="the city's page that lists the members")
    cmd.add_argument(
        "--platform",
        choices=list(_PLATFORM_LABELS),
        help="where the city keeps its meeting videos. Without it, setup uses the one platform "
        "it finds (--granicus, --cablecast, or the folder's sources.json) and otherwise asks; "
        "when it cannot ask, it stops",
    )
    cmd.add_argument("--granicus", metavar="HOST", help="Granicus host, e.g. oakland.granicus.com")
    cmd.add_argument("--view-id", type=int, help="Granicus ViewPublisher view_id")
    cmd.add_argument(
        "--cablecast", metavar="HOST", help="Cablecast host, e.g. reflect-oakland.cablecast.tv"
    )
    cmd.set_defaults(run=_setup)
    cmd = commands.add_parser("tools", help="install or update yt-dlp in CivicCast Brief's folder")
    cmd.add_argument(
        "--test-video",
        help="a public YouTube meeting video with captions (default: $CIVICCAST_RECORD_TEST_VIDEO)",
    )
    cmd.set_defaults(run=_tools)
    cmd = commands.add_parser("evidence", help="what the record holds for one meeting")
    which = cmd.add_mutually_exclusive_group(required=True)
    which.add_argument("--meeting", type=int, help="the meeting's id in the record")
    which.add_argument("--video", help="the meeting's YouTube video id")
    cmd.add_argument("--db", type=Path, help="the record database (default: the usual place)")
    cmd.add_argument("--check", type=Path, help="a draft to check, one sentence after another")
    cmd.add_argument("--out", type=Path, help="write the full result here as JSON")
    cmd.set_defaults(run=_evidence)

    from civiccast_brief.citybuild.cli import add_commands

    add_commands(commands)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
