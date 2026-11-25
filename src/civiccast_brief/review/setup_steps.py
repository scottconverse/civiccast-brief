"""The first-run checklist the Items page shows until the desk is set up.

Every step is read from the city folder's files and the station's run records, never a stored
"done" flag, so a step that is undone shows again. Each step reads the same file its page reads:

* ``city.json``: the City page (:func:`civiccast_brief.city.read_identity`).
* ``sources.json``: the Sources page (:func:`civiccast_brief.sources.load_sources`).
* ``city-check.json``: saved by ``city check`` and the desk's Check button
  (``citybuild.cli.PROOF_FILE``, not imported here because that module opens a browser).
* ``roster.json`` or ``rosters/*.json``: the Roster page
  (:func:`civiccast_brief.weekly.rosterfill.loaded_rosters`).
* ``models.json``: the Models page; ``voices.json``: the Voices page; ``weekly.json``: the
  Weekly job page.
* The station's run records: a finished run with trigger ``test`` and status ``ok``.

The Items page calls :func:`checklist` once per request: a handful of small file reads, no
network and no subprocess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from civiccast_brief import city as city_file
from civiccast_brief import models as model_settings
from civiccast_brief import sources as city_sources
from civiccast_brief.review import voices, weekly_job
from civiccast_brief.weekly import rosterfill

if TYPE_CHECKING:
    from civiccast_brief.station.runner import Station

#: The file the Check button and ``city check`` save (``citybuild.cli.PROOF_FILE``).
PROOF_NAME: Final = "city-check.json"
#: The run trigger and status of a finished Test one meeting run (``station.runner``).
TEST_TRIGGER: Final = "test"
OK_STATUS: Final = "ok"

START_HINT: Final = (
    "Double-click desk.cmd, or run "
    "python -m civiccast_brief.review serve --db PATH --city CITY_FOLDER."
)


@dataclass(frozen=True)
class Step:
    """One line of the checklist: what to do, whether it is done, and where to do it."""

    key: str
    title: str
    done: bool
    optional: bool
    href: str
    hint: str


@dataclass(frozen=True)
class Checklist:
    """The steps, the city's name and how many required steps are done."""

    steps: tuple[Step, ...]
    city: str

    @property
    def required_total(self) -> int:
        return sum(1 for step in self.steps if not step.optional)

    @property
    def required_done(self) -> int:
        return sum(1 for step in self.steps if step.done and not step.optional)

    @property
    def show(self) -> bool:
        """True while a required step is not done; an all-done setup shows nothing."""
        return any(not step.done for step in self.steps if not step.optional)


def steps(city_dir: str | Path | None, station: Station | None) -> list[Step]:
    """The checklist's steps, in the order the set-up guide gives them."""
    return list(checklist(city_dir, station).steps)


def checklist(city_dir: str | Path | None, station: Station | None) -> Checklist:
    """The checklist, reading each file once."""
    if city_dir is None:
        return Checklist(
            (
                Step(
                    "start",
                    "Start the desk with a city folder",
                    False,
                    False,
                    "",
                    START_HINT,
                ),
            ),
            "",
        )
    folder = Path(city_dir)
    city_step, name = _city_step(folder)
    steps_out = (
        city_step,
        _sources_step(folder),
        _check_step(folder),
        _roster_step(folder),
        _models_step(folder),
        _voices_step(folder),
        _test_step(station),
        _weekly_step(folder),
    )
    return Checklist(steps_out, name)


def _city_step(folder: Path) -> tuple[Step, str]:
    """Whether city.json has the name and state the City page asks for."""
    try:
        identity = city_file.read_identity(folder)
    except city_file.CityError:
        identity = {}
    name = identity.get("name", "")
    done = bool(name) and bool(identity.get("state", ""))
    step = Step(
        "city",
        "City",
        done,
        False,
        "/city",
        "The city's name, short web name and state.",
    )
    return step, name


def _sources_step(folder: Path) -> Step:
    try:
        rows = city_sources.load_sources(folder)
    except city_sources.SourceError:
        rows = []
    return Step(
        "sources",
        "Sources",
        bool(rows),
        False,
        "/sources",
        "Where the city's meetings come from. Find a city's sources adds them.",
    )


def _check_step(folder: Path) -> Step:
    """Whether the Check button's saved city-check.json is there and readable."""
    done = False
    try:
        data = json.loads((folder / PROOF_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    else:
        done = isinstance(data, dict) and isinstance(data.get("results"), list)
    return Step(
        "check",
        "Check the sources",
        done,
        False,
        "/sources",
        "Read one recent meeting of each body, on the Sources page.",
    )


def _roster_step(folder: Path) -> Step:
    try:
        rosters = rosterfill.loaded_rosters(folder)
    except OSError:
        rosters = []
    return Step(
        "roster",
        "Roster",
        bool(rosters),
        False,
        "/roster",
        "Each body's members, which the weekly run reads from the records.",
    )


def _models_step(folder: Path) -> Step:
    done = False
    if (folder / model_settings.SETTINGS_NAME).is_file():
        try:
            model_settings.load_settings(folder)
        except model_settings.ModelSettingsError:
            pass
        else:
            done = True
    return Step(
        "models",
        "Models",
        done,
        True,
        "/settings",
        "Which writing model each task uses, per body.",
    )


def _voices_step(folder: Path) -> Step:
    done = False
    if (folder / voices.SETTINGS_NAME).is_file():
        try:
            voices.load(folder)
        except voices.VoicesError:
            pass
        else:
            done = True
    return Step(
        "voices",
        "Voices",
        done,
        True,
        "/voices",
        "The voice engine and the two host voices for the episodes.",
    )


def _test_step(station: Station | None) -> Step:
    done = station is not None and station.has_run(TEST_TRIGGER, OK_STATUS)
    return Step(
        "test",
        "Test one meeting",
        done,
        False,
        "/runs",
        "Run the newest meeting through the weekly job, from the Weekly job page.",
    )


def _weekly_step(folder: Path) -> Step:
    try:
        enabled = weekly_job.load(folder).enabled
    except weekly_job.WeeklyJobError:
        enabled = False
    return Step(
        "weekly",
        "Turn on the weekly job",
        enabled,
        False,
        "/runs",
        "Set a day and time on the Weekly job page, and save it with the schedule on.",
    )
