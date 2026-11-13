"""Command line for the weekly run.

    python -m civiccast_brief.weekly run --city cities/longmont --work runs/weekly --db desk.db

finds the last 7 days of meetings in the city's ``sources.json`` and runs each one (see
:mod:`civiccast_brief.weekly.run`). Add the site settings to export, voice and build the site::

    --episodes site-in --media media --site-out site
    --base-url https://longmont.civiccastbrief.org --media-base-url https://media.example.org

Exit 0 when every runnable meeting and the site steps finished; 1 when any failed. A meeting
that cannot be run (no video, cancelled, no body) is listed with the reason and is not a failure.
Rerun the same command to try the failed steps again.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from civiccast_brief.city import CityError, load_city
from civiccast_brief.sources import SourceError, load_sources
from civiccast_brief.weekly.documents import Fetcher, read_documents
from civiccast_brief.weekly.find import Agenda, Listers, find_meetings
from civiccast_brief.weekly.run import Settings, Site, run_week

HTML_AGENDA = 3


def real_listers(client: Any) -> Listers:
    """The readers for live portals, channels and archives, over one ``httpx.Client``."""
    from civiccast_brief.meeting import cablecast, granicus
    from civiccast_brief.record.adapters.primegov import PrimeGovAdapter
    from civiccast_brief.record.video.youtube.channel import channel_lister

    adapters: dict[str, PrimeGovAdapter] = {}

    def adapter(url: str) -> PrimeGovAdapter:
        if url not in adapters:
            adapters[url] = PrimeGovAdapter(url, client)
        return adapters[url]

    def primegov(url: str, first: date, last: date) -> list[Any]:
        return adapter(url).list_meetings(first, last)

    def primegov_agenda(url: str, meeting: Any) -> Agenda | None:
        for document in meeting.documents:
            if document.compile_output_type == HTML_AGENDA:
                portal = adapter(url)
                content = portal.get_html_agenda(document)
                items = portal.parse_html_agenda(content.content)
                lines = tuple(f"{item.number} {item.title}" for item in items)
                return Agenda(content.citation_url, lines) if lines else None
        return None

    fetchers: dict[str, Fetcher] = {}

    def primegov_documents(url: str, meeting: Any, body: str) -> tuple[dict[str, Any], list[str]]:
        if url not in fetchers:
            fetchers[url] = Fetcher(adapter(url))
        return read_documents(fetchers[url], meeting, body)

    from civiccast_brief.citybuild.fetch import Fetcher as PageFetcher
    from civiccast_brief.weekly import listed

    pages = PageFetcher(client)

    def documents(source: Any, first: date, last: date) -> list[Any]:
        return listed.list_docs(source, first, last, pages, listers.city)

    def read_listed(docs: Any) -> tuple[dict[str, Any], list[str]]:
        return listed.read_all(docs, pages)

    channel = channel_lister(client)
    listers = Listers(
        primegov=primegov,
        primegov_agenda=primegov_agenda,
        primegov_documents=primegov_documents,
        youtube=channel.list_videos,
        granicus=granicus.list_clips,
        cablecast=cablecast.list_shows,
        documents=documents,
        read_documents=read_listed,
    )
    return listers


def _site(args: argparse.Namespace, slug: str) -> Site | None:
    values = [args.episodes, args.media, args.site_out, args.base_url, args.media_base_url]
    if not any(values):
        return None
    if not all(values):
        raise ValueError(
            "the site steps need all of --episodes, --media, --site-out, --base-url "
            "and --media-base-url"
        )
    return Site(
        episodes=args.episodes,
        media=args.media,
        out=args.site_out,
        base_url=args.base_url,
        media_base_url=args.media_base_url,
        slug=slug,
        audio_engine=args.audio_engine or "",
    )


def _run(args: argparse.Namespace, listers: Listers | None = None) -> int:
    try:
        city = load_city(args.city)
        sources = load_sources(args.city)
        site = _site(args, city.slug)
    except (CityError, SourceError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not sources:
        print(f"error: {args.city} has no sources.json; add the city's sources", file=sys.stderr)
        return 1
    last = args.last or date.today()
    first = args.first or last - timedelta(days=args.days - 1)
    settings = Settings(city=args.city, work=args.work, db=args.db, diarize=args.diarize, site=site)
    if listers is not None:
        return _week(city.name, sources, first, last, listers, settings)
    import httpx

    # One client for the whole run: the documents step fetches packets inside run_week.
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        listers = real_listers(client)
        listers.city = city.name  # a title may leave the city's name off a body's name
        return _week(city.name, sources, first, last, listers, settings)


def _week(
    name: str, sources: Any, first: date, last: date, listers: Listers, settings: Settings
) -> int:
    meetings, problems = find_meetings(sources, first, last, listers)
    print(f"{name}: {len(meetings)} meeting(s) from {first} to {last}")
    for problem in problems:
        print(f"source problem: {problem}", file=sys.stderr)
    report = run_week(settings, meetings, last)
    for line in report.lines:
        print(line)
    return 1 if report.failed or problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m civiccast_brief.weekly")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="find this week's meetings and run each one")
    run.add_argument("--city", type=Path, required=True, help="city folder (sources.json ...)")
    run.add_argument("--work", type=Path, required=True, help="work folder (kept between runs)")
    run.add_argument("--db", type=Path, required=True, help="editor desk database")
    run.add_argument("--days", type=int, default=7, help="how many days back (default 7)")
    run.add_argument("--first", type=date.fromisoformat, help="first day, YYYY-MM-DD")
    run.add_argument("--last", type=date.fromisoformat, help="last day (default: today)")
    run.add_argument("--diarize", action="store_true", help="label caption lines with voices")
    run.add_argument("--episodes", type=Path, help="site input folder (city.json, episodes)")
    run.add_argument("--media", type=Path, help="folder for the MP3, chapters and VTT files")
    run.add_argument("--site-out", type=Path, help="folder the site is built into")
    run.add_argument("--base-url", help="public address of the site")
    run.add_argument("--media-base-url", help="where the media files will be served")
    run.add_argument("--audio-engine", help="voice engine (default: the audio command's)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
