"""``python -m civiccast_brief city find`` and ``city check``.

    city find --name Longmont --state CO [--website URL] [--also "Body | URL"]... --out FOLDER
    city check --city FOLDER [--body NAME]... [--captions]

``find`` searches and writes the stubs and sources into the folder (``--dry-run`` only prints).
``check`` reads one recent meeting of each body from its sources and prints what it read.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from civiccast_brief.citybuild import browser
from civiccast_brief.citybuild.build import build_city
from civiccast_brief.citybuild.fetch import Fetcher
from civiccast_brief.citybuild.read import Proof, read_latest
from civiccast_brief.citybuild.search import search_city
from civiccast_brief.sources import SourceError, load_sources

PROOF_FILE = "city-check.json"


def parse_also(values: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        if "|" not in value:
            raise ValueError(f'--also {value!r}: write it as "Body name | https://..."')
        body, url = value.split("|", 1)
        pairs.append((body.strip(), url.strip()))
    return pairs


def _find(args: argparse.Namespace, fetcher: Fetcher | None = None) -> int:
    try:
        extra = parse_also(args.also or [])
    except ValueError as exc:
        print(f"city find: {exc}", file=sys.stderr)
        return 2
    fetcher = fetcher or Fetcher(
        log=(lambda line: print(line, file=sys.stderr)) if args.verbose else (lambda _l: None)
    )
    found = search_city(args.name, args.state, website=args.website, extra=extra, fetcher=fetcher)
    for line in found.report():
        print(line)
    print(f"({len(found.asked)} web requests, no model)")
    if args.dry_run or args.out is None:
        if args.out is None:
            print("not written: give --out FOLDER to write the city folder")
        return 0 if found.candidates else 1
    try:
        built = build_city(args.out, found, fetcher=fetcher, open_in_browser=browser.open_page)
    except (OSError, SourceError) as exc:
        print(f"city find: not written: {exc}", file=sys.stderr)
        return 1
    finally:
        # A members page that opens only in a browser opens Chrome; it closes here.
        browser.close_shared()
    for line in built.lines:
        print(line)
    for line in built.to_fill:
        print(f"to fill in: {line}")
    return 0 if found.candidates else 1


def check_city(
    folder: Path,
    *,
    bodies: list[str] | None = None,
    captions: bool = False,
    fetcher: Fetcher | None = None,
    today: date | None = None,
) -> list[Proof]:
    """Read each body's newest meeting from each source that lists it."""
    fetcher = fetcher or Fetcher()
    proofs: list[Proof] = []
    try:
        for source in load_sources(folder):
            for body in source.bodies:
                if bodies and body.casefold() not in {b.casefold() for b in bodies}:
                    continue
                proofs.append(
                    read_latest(
                        source,
                        body,
                        fetcher,
                        today=today,
                        captions=captions,
                        work_dir=folder / "city-check",
                    )
                )
    finally:
        # A Laserfiche folder opens Chrome; the window closes when the check is done.
        browser.close_shared()
    return proofs


def _check(args: argparse.Namespace, fetcher: Fetcher | None = None) -> int:
    try:
        proofs = check_city(args.city, bodies=args.body, captions=args.captions, fetcher=fetcher)
    except SourceError as exc:
        print(f"city check: {exc}", file=sys.stderr)
        return 1
    for proof in proofs:
        for line in proof.report():
            print(line)
    read = sum(p.ok for p in proofs)
    print(f"{read} of {len(proofs)} body sources read")
    data: dict[str, Any] = {
        "checked": date.today().isoformat(),
        "results": [p.to_json() for p in proofs],
    }
    out = args.city / PROOF_FILE
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved {out}")
    return 0 if proofs and read == len(proofs) else 1


def add_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    city = commands.add_parser("city", help="find a city's meeting sources, or check them")
    sub = city.add_subparsers(dest="city_command", required=True)
    cmd = sub.add_parser("find", help="search the city's site and the meeting platforms")
    cmd.add_argument("--name", required=True, help="the city's name, e.g. Longmont")
    cmd.add_argument("--state", required=True, help="the state, e.g. CO or Colorado")
    cmd.add_argument("--website", help="the city's website, when the guess would miss it")
    cmd.add_argument(
        "--also",
        action="append",
        metavar='"BODY | URL"',
        help="a page or Drive folder for a body with no platform, like a school board (repeat it)",
    )
    cmd.add_argument("--out", type=Path, help="the city folder to write")
    cmd.add_argument("--dry-run", action="store_true", help="print what was found, write nothing")
    cmd.add_argument("--verbose", action="store_true", help="print each web request")
    cmd.set_defaults(run=_find)
    cmd = sub.add_parser("check", help="read one recent meeting of each body from its sources")
    cmd.add_argument("--city", type=Path, required=True, help="the city folder")
    cmd.add_argument("--body", action="append", help="check only this body (repeat it)")
    cmd.add_argument(
        "--captions", action="store_true", help="also fetch YouTube captions (paced, slow)"
    )
    cmd.set_defaults(run=_check)
