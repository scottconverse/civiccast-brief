"""``python -m civiccast_brief.review run-due --cities DIR``: the Windows task's command.

For each city folder, in order, build its Station the same way the desk does and start its
weekly run when one is due. The run happens in this thread (``Station.run_once`` through
``tick``) and the command waits for it, so the next city starts only when this one is done and
the scheduled task never leaves runs behind it.

Nothing else is started: a city whose schedule is off is skipped, a city with a broken
weekly.json is reported and skipped, and a city another run is already working (an open desk)
prints ``busy``. One city's failure never stops the rest. Every city's runs are recorded in its
own desk.db, so the desk's Weekly job page shows them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from civiccast_brief.review import weekly_job
from civiccast_brief.review.hub import city_slugs
from civiccast_brief.review.web import city_station
from civiccast_brief.station.runner import Station

#: Builds a city's Station; tests replace it with one whose steps are fakes.
StationFor = Callable[..., Station]


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:300] or exc.__class__.__name__


def run_due(
    cities_dir: str | Path,
    *,
    out: Callable[[str], None] = print,
    station_for: StationFor = city_station,
) -> int:
    """Run every city that is due, one at a time. Returns 0 unless the command itself failed."""
    cities = Path(cities_dir)
    for slug in city_slugs(cities):
        folder = cities / slug
        city_dir = folder / "city"
        try:
            settings = weekly_job.load(city_dir)
        except weekly_job.WeeklyJobError as exc:
            out(f"{slug}: broken weekly.json: {_one_line(exc)}")
            continue
        if not settings.enabled:
            out(f"{slug}: schedule off")
            continue
        try:
            station = station_for(
                folder / "desk.db", city_dir, weekly_work=folder / "runs" / "weekly"
            )
            out(_run_city(slug, station))
        except Exception as exc:  # noqa: BLE001 - one city's failure must not stop the rest
            out(f"{slug}: failed: {_one_line(exc)}")
    return 0


def _run_city(slug: str, station: Station) -> str:
    """Run the city when due, and say in one line what happened."""
    if not station.publish_due() and not (station.scheduled and station.due()):
        return f"{slug}: not due"
    run_id = station.tick()
    if run_id is None:
        return f"{slug}: busy (another run is going)"
    run = station.run(run_id)
    return f"{slug}: ran ({run.status if run is not None else 'failed'})"
