"""The setup wizard's city lines, saved setup files and one-at-a-time background jobs.

The hub's ``/setup`` pages keep the wizard's state in the city folder's files, never in a stored
"done" flag:

* ``DIR/<slug>/setup.json``: the name, state and website typed on the Cities page. It is the only
  file the wizard adds, and only so the Find step has the state and website to search with.
* ``DIR/<slug>/city/sources.json``: the Find step.
* ``DIR/<slug>/city/city-check.json``: the Check step (``citybuild.cli.save_proofs``).
* ``DIR/<slug>/city/roster.json`` or ``rosters/*.json``: the Rosters step.
* ``models.json`` and ``voices.json``: shown on the last step, and copied to other cities on
  request.

:class:`SetupJobs` runs Find and Check in one background thread, because Chrome and the web
fetcher do not like two searches at once. Tests inject every outside call.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

from civiccast_brief import models as model_settings
from civiccast_brief import setup_check
from civiccast_brief import sources as city_sources
from civiccast_brief.citybuild.search import STATES, slug_of, state_name
from civiccast_brief.review import voices
from civiccast_brief.weekly import rosterfill

#: The one file the wizard writes besides the city's own files.
SETUP_NAME: Final = "setup.json"
#: The saved check file (``citybuild.cli.PROOF_FILE``).
PROOF_NAME: Final = "city-check.json"

#: The wizard's steps, in order: (key, label). The key is also the page's path under /setup.
STEPS: Final = (
    ("computer", "This computer"),
    ("cities", "Cities"),
    ("find", "Find sources"),
    ("check", "Check sources"),
    ("rosters", "Rosters"),
    ("models-voices", "Models and voices"),
    ("website", "Website"),
    ("test", "Test"),
    ("weekly", "Every week"),
    ("done", "Everything works"),
)


@dataclass(frozen=True)
class ParsedCity:
    """One good line of the Cities textarea."""

    name: str
    state: str
    website: str = ""


@dataclass(frozen=True)
class LineError:
    """One bad line: its number (counting from 1), the text, and why it is bad."""

    line: int
    text: str
    why: str


@dataclass(frozen=True)
class CitySetup:
    """What the Find step needs to search for one city."""

    name: str
    state: str
    website: str = ""


def computer_ready(checks: list[setup_check.Check], *, require_signin: bool) -> bool:
    """Every required row OK, one voice engine ready, and (on the page's Next) a real sign-in.

    The ``/setup`` redirect passes ``require_signin=False``: a row that has not been checked yet
    does not send the user back to This computer on every visit, and the page itself still keeps
    Next until the sign-in row is True.
    """
    for check in checks:
        if check.need != "required":
            continue
        if check.key == "claude_signin" and not require_signin:
            if check.ok is False:
                return False
            continue
        if check.ok is not True:
            return False
    return setup_check.voice_ok(checks)


def blocking_rows(checks: list[setup_check.Check]) -> list[str]:
    """What stops the This computer step's Next, in plain words ("" when nothing does)."""
    out: list[str] = []
    for check in checks:
        if check.need != "required" or check.ok is True:
            continue
        if check.key == "claude_signin" and check.ok is None:
            out.append("Claude sign-in (press Check Claude sign-in)")
        else:
            out.append(check.label)
    if not setup_check.voice_ok(checks):
        out.append("a ready voice engine")
    return out


@dataclass(frozen=True)
class CitySpec:
    """One city for a background job: its slug, its folders, and its setup values."""

    slug: str
    folder: Path
    setup: CitySetup

    @property
    def city_dir(self) -> Path:
        return self.folder / "city"


# ----- the Cities textarea -------------------------------------------------------------------


def us_state(text: str) -> str:
    """A two-letter code or a full state name, as the full name; raises ValueError otherwise.

    The same rule :func:`civiccast_brief.city.check_identity` uses: ``state_name`` turns ``CO``
    into ``Colorado``, and the result's letters must be one of the known states' slugs.
    """
    state = state_name(text)
    if not state or slug_of(state) not in set(STATES.values()):
        raise ValueError(f"{text.strip()!r} is not a US state; write CO or Colorado.")
    return state


def _website(text: str) -> str:
    """A website must be an http or https link, so the Find step can open it."""
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"The website {text!r} must start with http:// or https://.")
    return text


def parse_cities(text: str) -> tuple[list[ParsedCity], list[LineError]]:
    """Each non-blank line as ``City, ST`` or ``City, ST, https://website``, or its error.

    Bad lines are returned with their line numbers and reasons; the good ones are still used, so
    one typo does not throw away the rest of a pasted county.
    """
    good: list[ParsedCity] = []
    bad: list[LineError] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",", 2)]
        name = parts[0]
        if not name:
            bad.append(LineError(number, line, "The city's name is missing."))
            continue
        if len(parts) < 2 or not parts[1]:
            bad.append(LineError(number, line, "The state is missing; write CO or Colorado."))
            continue
        try:
            state = us_state(parts[1])
        except ValueError as exc:
            bad.append(LineError(number, line, str(exc)))
            continue
        website = ""
        if len(parts) == 3 and parts[2]:
            try:
                website = _website(parts[2])
            except ValueError as exc:
                bad.append(LineError(number, line, str(exc)))
                continue
        good.append(ParsedCity(name, state, website))
    return good, bad


# ----- what the steps read -------------------------------------------------------------------


def write_setup(folder: str | Path, setup: CitySetup) -> Path:
    """Save ``setup.json`` in the city's slug folder (``DIR/<slug>/``)."""
    out = Path(folder) / SETUP_NAME
    data = {"name": setup.name, "state": setup.state, "website": setup.website}
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def read_setup(folder: str | Path) -> CitySetup:
    """The typed values, else the name and state from the city's own files."""
    folder = Path(folder)
    name = state = website = ""
    try:
        data = json.loads((folder / SETUP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        name = str(data.get("name") or "").strip()
        state = str(data.get("state") or "").strip()
        website = str(data.get("website") or "").strip()
    if not name or not state:
        city_dir = folder / "city"
        for file_name in ("city.json", "roster.json"):
            try:
                own = json.loads((city_dir / file_name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(own, dict):
                name = name or str(own.get("name") or "").strip()
                state = state or str(own.get("state") or "").strip()
    return CitySetup(name, state, website)


def has_sources(city_dir: str | Path) -> bool:
    try:
        return bool(city_sources.load_sources(Path(city_dir)))
    except city_sources.SourceError:
        return False


def source_count(city_dir: str | Path) -> int:
    try:
        return len(city_sources.load_sources(Path(city_dir)))
    except city_sources.SourceError:
        return 0


def read_proofs(city_dir: str | Path) -> list[dict[str, Any]] | None:
    """The saved ``city-check.json`` results, or None when it is missing or unreadable."""
    try:
        data = json.loads((Path(city_dir) / PROOF_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None
    return [row for row in data["results"] if isinstance(row, dict)]


def check_all_read(proofs: list[dict[str, Any]] | None) -> bool:
    """True when a check was saved and every body in it was read."""
    return bool(proofs) and all(row.get("ok") is True for row in proofs or [])


def rosters(city_dir: str | Path) -> list[dict[str, Any]]:
    try:
        return rosterfill.loaded_rosters(Path(city_dir))
    except OSError:
        return []


def model_rows(city_dir: str | Path | None) -> list[dict[str, str]]:
    """Each task's default pick and any body overrides, from models.json or the defaults."""
    settings = model_settings.ModelSettings()
    if city_dir is not None and (Path(city_dir) / model_settings.SETTINGS_NAME).is_file():
        try:
            settings = model_settings.load_settings(city_dir)
        except model_settings.ModelSettingsError:
            pass
    rows: list[dict[str, str]] = []
    for task in model_settings.TASKS:
        picks = settings.tasks.get(task) or model_settings.default_picks()
        bodies = ", ".join(f"{body}: {pick}" for body, pick in sorted(picks.bodies.items()))
        rows.append(
            {"task": task, "default": picks.default or model_settings.DEEPSEEK, "bodies": bodies}
        )
    return rows


def voice_summary(city_dir: str | Path | None) -> dict[str, Any]:
    """The engine and each host's voice, from voices.json or the built-in defaults."""
    chosen = voices.Voices()
    if city_dir is not None and (Path(city_dir) / voices.SETTINGS_NAME).is_file():
        try:
            chosen = voices.load(city_dir)
        except voices.VoicesError:
            chosen = voices.Voices()
    engine = chosen.chosen_engine
    fields = voices.VOICE_FIELDS.get(engine, ())
    audio = dict(chosen.audio)
    spoken = {field_name: str(audio.get(field_name, "") or "") for field_name in fields}
    return {
        "engine": voices.LABELS.get(engine, engine),
        "fallback": voices.LABELS.get(chosen.fallback_engine, chosen.fallback_engine or "None"),
        "voices": spoken,
    }


# ----- copying models and voices -------------------------------------------------------------

COPY_FILES: Final = (model_settings.SETTINGS_NAME, voices.SETTINGS_NAME)


def copy_settings(source_city_dir: str | Path, target_dirs: Iterable[str | Path]) -> list[str]:
    """Copy the first city's models.json and voices.json to every other city that lacks a
    different one. Files that already match are left alone, and the source city is never
    written. Returns one line per city changed (or why nothing was)."""
    source = Path(source_city_dir)
    lines: list[str] = []
    for target in target_dirs:
        target = Path(target)
        if target == source:
            continue
        copied: list[str] = []
        for name in COPY_FILES:
            origin = source / name
            if not origin.is_file():
                continue
            data = origin.read_bytes()
            here = target / name
            if here.is_file() and here.read_bytes() == data:
                continue
            tmp = here.with_name(here.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(here)
            copied.append(name)
        if copied:
            lines.append(f"{target.parent.name}: copied {', '.join(copied)}.")
    return lines


# ----- the background jobs -------------------------------------------------------------------


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:200] or exc.__class__.__name__


def _real_find(city: CitySpec, *, fetcher: Any, open_page: Any) -> int:
    """Search and save every found source, as the desk's Find page does when all are ticked."""
    from civiccast_brief.citybuild.build import build_city
    from civiccast_brief.citybuild.search import search_city

    found = search_city(
        city.setup.name,
        city.setup.state,
        website=city.setup.website or None,
        fetcher=fetcher,
        open_in_browser=open_page,
    )
    build_city(
        city.city_dir,
        found,
        chosen=None,
        slug=city.slug,
        fetcher=fetcher,
        open_in_browser=open_page,
    )
    return len(found.candidates)


def _real_check(city: CitySpec, *, fetcher: Any) -> None:
    """Read every body once and save city-check.json, as the desk's Check button does."""
    from civiccast_brief.citybuild.cli import check_city, save_proofs

    proofs = check_city(city.city_dir, captions=False, fetcher=fetcher)
    save_proofs(city.city_dir, proofs)


def _default_fetcher() -> Any:
    from civiccast_brief.citybuild.fetch import Fetcher

    return Fetcher()


def _default_close() -> None:
    from civiccast_brief.citybuild import browser

    browser.close_shared()


def _default_open_page(url: str) -> Any:
    from civiccast_brief.citybuild import browser

    return browser.open_page(url)


@dataclass(frozen=True)
class CityStatus:
    """One city's row on a progress page."""

    slug: str
    state: str = "waiting"
    message: str = ""
    count: int = 0


class SetupJobs:
    """One background thread for Find or Check, with a status row per city.

    A second start while a job runs does nothing and returns False, so the page simply keeps
    showing progress: Chrome must not run two searches at once. Every outside call (the search,
    the build, the check, the fetcher, opening and closing Chrome) can be injected.
    """

    def __init__(
        self,
        *,
        find_city: Callable[..., int] = _real_find,
        check_city: Callable[..., None] = _real_check,
        fetcher_factory: Callable[[], Any] = _default_fetcher,
        open_page: Callable[[str], Any] = _default_open_page,
        close_chrome: Callable[[], None] = _default_close,
    ) -> None:
        self._find_city = find_city
        self._check_city = check_city
        self._fetcher = fetcher_factory
        self._open_page = open_page
        self._close = close_chrome
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._kind = ""
        self._rows: dict[str, CityStatus] = {}

    # ----- starting ---------------------------------------------------------------------

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def kind(self) -> str:
        return self._kind

    def start_find(self, cities: list[CitySpec]) -> bool:
        return self._start("find", cities)

    def start_check(self, cities: list[CitySpec]) -> bool:
        return self._start("check", cities)

    def _start(self, kind: str, cities: list[CitySpec]) -> bool:
        with self._lock:
            if self.running():
                return False
            self._kind = kind
            self._rows = {city.slug: CityStatus(city.slug) for city in cities}
            self._thread = threading.Thread(
                target=self._run, args=(kind, list(cities)), daemon=True
            )
            self._thread.start()
            return True

    # ----- running ----------------------------------------------------------------------

    def _set(self, slug: str, state: str, *, message: str = "", count: int = 0) -> None:
        with self._lock:
            if slug in self._rows:
                self._rows[slug] = CityStatus(slug, state, message, count)

    def _run(self, kind: str, cities: list[CitySpec]) -> None:
        for city in cities:
            self._set(city.slug, "searching" if kind == "find" else "checking")
            try:
                fetcher = self._fetcher()
                if kind == "find":
                    count = self._find_city(city, fetcher=fetcher, open_page=self._open_page)
                    self._set(city.slug, "saved" if count else "nothing", count=count)
                else:
                    self._check_city(city, fetcher=fetcher)
                    self._set(city.slug, "checked")
            except Exception as exc:  # noqa: BLE001 - one city's failure must not stop the rest
                self._set(city.slug, "failed", message=_one_line(exc))
            finally:
                with suppress(Exception):  # closing Chrome is best effort
                    self._close()

    def status(self) -> list[CityStatus]:
        with self._lock:
            return list(self._rows.values())


# ----- Test every city -----------------------------------------------------------------------


def last_line(text: str) -> str:
    """The last non-empty line of a run's output, cut short for a status cell."""
    for line in reversed(str(text or "").splitlines()):
        if line.strip():
            return line.strip()[:300]
    return ""


@dataclass(frozen=True)
class TestCityStatus:
    """One city's row on the Test every city page."""

    slug: str
    state: str = "waiting"
    message: str = ""
    run_id: int | None = None


class TestJobs:
    """One background thread that tests each city, one city at a time.

    For each city it starts that city's Test one meeting run (the same steps as the desk's
    button) through its Station, waits for the ``station_runs`` row to finish, then checks the
    city is done: the run ended ``ok`` and at least one item is waiting for review. Every
    outside call is injected, so tests give fake Stations and never run a model.

    A second start while a job runs does nothing and returns False.
    """

    def __init__(
        self,
        *,
        station_for: Callable[[CitySpec], Any],
        steps_for: Callable[[CitySpec], list[list[str]]],
        review_count: Callable[[CitySpec], int],
        poll_seconds: float = 1.0,
        timeout_seconds: float = 2 * 60 * 60,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._station_for = station_for
        self._steps_for = steps_for
        self._review_count = review_count
        self._poll = poll_seconds
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._rows: dict[str, TestCityStatus] = {}

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, cities: list[CitySpec]) -> bool:
        with self._lock:
            if self.running():
                return False
            self._rows = {city.slug: TestCityStatus(city.slug) for city in cities}
            self._thread = threading.Thread(
                target=self._run, args=(list(cities),), daemon=True, name="setup-test"
            )
            self._thread.start()
            return True

    def status(self) -> list[TestCityStatus]:
        with self._lock:
            return list(self._rows.values())

    def row(self, slug: str) -> TestCityStatus | None:
        with self._lock:
            return self._rows.get(slug)

    def _set(self, slug: str, state: str, *, message: str = "", run_id: int | None = None) -> None:
        with self._lock:
            if slug in self._rows:
                self._rows[slug] = TestCityStatus(slug, state, message, run_id)

    def _run(self, cities: list[CitySpec]) -> None:
        for city in cities:
            self._set(city.slug, "running")
            try:
                station = self._station_for(city)
                run_id = station.run_now_with(self._steps_for(city), "test")
                if run_id is None:
                    self._set(city.slug, "failed", message="Another run is going for this city.")
                    continue
                run = self._wait(station, run_id)
                if run is None:
                    self._set(
                        city.slug,
                        "failed",
                        message="The test run did not finish in time.",
                        run_id=run_id,
                    )
                    continue
                if run.status != "ok":
                    message = last_line(run.log) or f"The run {run.status}."
                    self._set(city.slug, "failed", message=message, run_id=run_id)
                    continue
                if self._review_count(city) < 1:
                    self._set(
                        city.slug,
                        "failed",
                        message="The test run finished, but no item waits for review.",
                        run_id=run_id,
                    )
                    continue
                self._set(city.slug, "ok", run_id=run_id)
            except Exception as exc:  # noqa: BLE001 - one city's failure must not stop the rest
                self._set(city.slug, "failed", message=_one_line(exc))

    def _wait(self, station: Any, run_id: int) -> Any:
        end = self._monotonic() + self._timeout
        while True:
            run = station.run(run_id)
            if run is None or run.status != "running":
                return run
            if self._monotonic() >= end:
                return None
            self._sleep(self._poll)
