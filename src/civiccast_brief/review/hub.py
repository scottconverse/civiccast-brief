"""Several cities in one desk window, with one sign-in.

Layout: ``--cities DIR`` holds one subfolder per city::

    DIR/<slug>/city/            the city folder (city.json, roster.json, sources.json)
    DIR/<slug>/desk.db          that city's items, and its weekly job's runs
    DIR/<slug>/runs/weekly/     that city's weekly work folder

``<slug>`` is the city folder's short web name (``city.py``'s rule). The hub's own ``--db``
holds only the accounts; every city desk shares one :class:`DeskAuth`, and the session cookie
is ``Path=/``, so signing in at the hub covers every city.

Each city is mounted at ``/c/<slug>/`` through a small dispatcher: it looks the slug up when
the request arrives, so a city added while the hub runs answers without a restart. The hub
starts and stops each city's Station itself, because Starlette does not run a mounted app's
lifespan.
"""

from __future__ import annotations

import hmac
import re
import secrets
import sqlite3
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from jinja2 import Environment, FileSystemLoader
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from civiccast_brief import city as city_file
from civiccast_brief import setup_check
from civiccast_brief.review import setup_wizard, site_settings, weekly_job, weekly_task
from civiccast_brief.review.auth import SESSION_COOKIE, SESSION_SECONDS, AuthError, DeskAuth
from civiccast_brief.review.store import ReviewError, ReviewStore
from civiccast_brief.review.web import (
    MAX_FORM_BYTES,
    OPEN_PATHS,
    SECURITY_HEADERS,
    STATIC_DIR,
    TEMPLATE_DIR,
    _address,
    _cross_site,
    _host_allowed,
    _redirect,
    _route_path,
    city_desk,
)
from civiccast_brief.station.runner import WEEKDAYS, Station, StationError


class HubError(ValueError):
    """A city cannot be added; the message says why."""


#: What the Every week page says after a successful action.
WEEKLY_NOTICES: Final = {
    "saved": "Saved. Every city runs on that schedule.",
    "installed": "The Windows task is set up.",
    "removed": "The Windows task is removed.",
}


@dataclass
class CityDesk:
    """One city's open store, its Station and its mounted desk app."""

    slug: str
    folder: Path
    store: ReviewStore
    station: Station
    app: FastAPI

    @property
    def city_dir(self) -> Path:
        return self.folder / "city"


def slug_from_name(name: str) -> str:
    """The city folder name for a city name: lowercase words joined by single hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def city_slugs(cities_dir: str | Path) -> list[str]:
    """Every subfolder that holds a ``city/`` folder and a valid slug, by folder name.

    ``_left-out`` holds cities the wizard left out, so it is never one of them."""
    try:
        entries = sorted(Path(cities_dir).iterdir())
    except OSError:
        return []
    return [
        entry.name
        for entry in entries
        if entry.is_dir() and city_file._SLUG_RE.match(entry.name) and (entry / "city").is_dir()
    ]


class Hub:
    """The cities and their desks, discovered from the folder and opened when needed."""

    def __init__(self, hub_db: str | Path, cities_dir: str | Path) -> None:
        self.hub_db = Path(hub_db)
        self.cities_dir = Path(cities_dir)
        self.cities_dir.mkdir(parents=True, exist_ok=True)
        #: One account store for every city desk; the session cookie is Path=/.
        self.accounts = DeskAuth(self.hub_db)
        self.token = secrets.token_urlsafe(24)
        self.templates = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
        self.desks: dict[str, CityDesk] = {}
        #: The setup wizard's one background job (Find or Check), shared by every city.
        self.setup_jobs = setup_wizard.SetupJobs()
        #: The setup wizard's Test every city job, one city at a time.
        self.test_jobs = setup_wizard.TestJobs(
            station_for=lambda city: self.load(city.slug).station,
            steps_for=self._test_steps,
            review_count=self._review_count,
        )
        #: Runs PowerShell for the Windows task page; tests give a fake.
        self.task_runner = weekly_task.run_powershell
        #: True between lifespan startup and shutdown, when loaded cities' Stations run.
        self.running = False

    def token_ok(self, form: dict[str, str]) -> bool:
        given = str(form.get("token", ""))
        return hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))

    # ----- discovery ------------------------------------------------------------------------

    def slugs(self) -> list[str]:
        """Every subfolder that holds a ``city/`` folder and a valid slug, by folder name."""
        return city_slugs(self.cities_dir)

    def rows(self) -> list[dict[str, str]]:
        """The city list page's rows: name and state from city.read_identity, else the slug."""
        out: list[dict[str, str]] = []
        for slug in self.slugs():
            identity = city_file.read_identity(self.cities_dir / slug / "city")
            out.append(
                {
                    "slug": slug,
                    "name": identity.get("name") or slug,
                    "state": identity.get("state", ""),
                }
            )
        return out

    def switcher(self) -> list[tuple[str, str]]:
        """(slug, display name) for every city, for the header's city switcher."""
        return [(row["slug"], row["name"]) for row in self.rows()]

    # ----- the setup wizard's Test step -----------------------------------------------------

    def _test_steps(self, city: setup_wizard.CitySpec) -> list[list[str]]:
        """The Test one meeting command for a city, exactly as the desk's button builds it."""
        desk = self.load(city.slug)
        try:
            settings = weekly_job.load(desk.city_dir)
        except weekly_job.WeeklyJobError:
            settings = weekly_job.DEFAULT
        return weekly_job.test_steps(
            settings, desk.city_dir, desk.store.path, desk.folder / "runs" / "weekly"
        )

    def _review_count(self, city: setup_wizard.CitySpec) -> int:
        return len(self.load(city.slug).store.list_items("review"))

    def test_state(self, city: setup_wizard.CitySpec) -> tuple[str, str, int | None]:
        """A city's Test row when no job is running: (state, message, run id).

        Read from the city's own ``station_runs`` and desk: the newest test run must have
        ended ``ok`` and at least one item must wait for review."""
        desk = self.load(city.slug)
        job_row = self.test_jobs.row(city.slug)
        if job_row is not None:
            return job_row.state, job_row.message, job_row.run_id
        try:
            runs = [run for run in desk.station.runs() if run.trigger == "test"]
        except Exception:  # noqa: BLE001 - a station without run history is simply untested
            runs = []
        newest = runs[0] if runs else None
        if newest is None:
            return "waiting", "", None
        if newest.status == "running":
            return "running", "", newest.id
        if newest.status == "ok":
            if self._review_count(city):
                return "ok", "", newest.id
            return "failed", "The test run finished, but no item waits for review.", newest.id
        return (
            "failed",
            setup_wizard.last_line(newest.log) or f"The run {newest.status}.",
            newest.id,
        )

    def test_done(self, city: setup_wizard.CitySpec) -> bool:
        return self.test_state(city)[0] == "ok"

    # ----- cities ---------------------------------------------------------------------------

    def load(self, slug: str) -> CityDesk:
        """Open a city's store and build its desk app, once. A newly loaded Station starts at
        once when the hub is already running."""
        if slug in self.desks:
            return self.desks[slug]
        folder = self.cities_dir / slug
        store = ReviewStore(folder / "desk.db")
        try:
            app, station = city_desk(
                store,
                folder / "city",
                weekly_work=folder / "runs" / "weekly",
                auth=self.accounts,
                root=f"/c/{slug}",
                cities=self.switcher,
                current=slug,
            )
        except (ReviewError, StationError, OSError, sqlite3.Error):
            store.close()
            raise
        desk = CityDesk(slug, folder, store, station, app)
        self.desks[slug] = desk
        if self.running:
            station.start()
        return desk

    def ensure(self, slug: str) -> CityDesk | None:
        """A city's desk, opening it if its folder is valid; None for an unknown slug."""
        if slug in self.desks:
            return self.desks[slug]
        if not city_file._SLUG_RE.match(slug):
            return None
        if not (self.cities_dir / slug / "city").is_dir():
            return None
        return self.load(slug)

    def add(self, name: str, state: str = "") -> str:
        """Make a new city's folders and mount it; returns its slug. ``state`` only prefills
        the Find page, so nothing is written into the city folder yet."""
        slug = slug_from_name(name)
        if not slug or not city_file._SLUG_RE.match(slug):
            raise HubError(
                "Enter the city's name, like Longmont or Fort Collins. The folder name is made "
                "from it with lowercase letters, digits and single hyphens."
            )
        folder = self.cities_dir / slug
        if folder.exists():
            raise HubError(f"A city named {name.strip()!r} is already here ({slug}).")
        (folder / "city").mkdir(parents=True)
        (folder / "runs" / "weekly").mkdir(parents=True)
        self.load(slug)
        return slug

    # ----- lifespan -------------------------------------------------------------------------

    def start(self) -> None:
        """Start every city's Station, opening each city's desk first."""
        try:
            for slug in self.slugs():
                self.load(slug).station.start()
            # Only now, so load() during startup does not also start each Station.
            self.running = True
        except (ReviewError, StationError, OSError, sqlite3.Error):
            self.stop()
            raise

    def stop(self) -> None:
        """Stop every Station and close every city's store and the accounts."""
        self.running = False
        for desk in self.desks.values():
            desk.station.stop()
            desk.store.close()
        self.desks.clear()
        self.accounts.close()


class CityDispatcher:
    """The ASGI app mounted at ``/c``: pick the city by slug, then hand the request to its app.

    Starlette's Mount strips ``/c`` and sets ``root_path`` to it before calling this. The city's
    desk sees ``root_path`` ``/c/<slug>`` and the path inside the city, as a mounted app would.
    The lookup happens on every request, so a city added later answers without a restart.
    """

    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise RuntimeError(f"{scope['type']} is not served under /c")
        path = str(scope.get("path", ""))
        root = str(scope.get("root_path", "")).rstrip("/")
        if root and path.startswith(root):
            path = path[len(root) :]
        slug, _, rest = path.lstrip("/").partition("/")
        desk = self.hub.ensure(slug) if slug else None
        if desk is None:
            response = PlainTextResponse("No city here.\n", status_code=404)
            await response(scope, receive, send)
            return
        child = dict(scope)
        child["root_path"] = f"{root}/{slug}"
        child["path"] = "/" + rest
        await desk.app(child, receive, send)


def make_hub_app(hub_db: str | Path, cities_dir: str | Path) -> FastAPI:
    """The desk for several cities: a city list, Add a city, and ``/c/<slug>/`` per city.

    ``hub_db`` holds the accounts only; each city's items live in its own ``desk.db``. One
    sign-in covers every city. The lifespan starts each city's Station and stops them all, and
    closes every store, on shutdown."""
    hub = Hub(hub_db, cities_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        hub.start()
        try:
            yield
        finally:
            hub.stop()

    app = FastAPI(
        title="CivicCast Brief editor desk",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.hub = hub
    app.state.token = hub.token

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not _host_allowed(request):
            response: Response = PlainTextResponse(
                "This desk answers only to 127.0.0.1 or localhost.\n", status_code=400
            )
        elif request.method not in ("GET", "POST", "HEAD"):
            response = PlainTextResponse(
                "Use GET or POST.\n", status_code=405, headers={"Allow": "GET, POST"}
            )
        elif int(request.headers.get("content-length") or 0) > MAX_FORM_BYTES:
            response = PlainTextResponse("The form is too large.\n", status_code=413)
        elif _cross_site(request):
            response = PlainTextResponse(
                "Forbidden: cross-site request blocked.\n", status_code=403
            )
        elif _route_path(request) in OPEN_PATHS:
            response = await call_next(request)
        else:
            user = await run_in_threadpool(
                hub.accounts.user_for, request.cookies.get(SESSION_COOKIE)
            )
            if user is not None:
                request.state.user = user
                response = await call_next(request)
            elif request.method == "POST":
                response = PlainTextResponse("Sign in first: /sign-in\n", status_code=401)
            else:
                response = _redirect("/sign-in")
        response.headers.update(SECURITY_HEADERS)
        return response

    async def read_form(request: Request) -> dict[str, str] | None:
        form = {k: str(v) for k, v in (await request.form()).items()}
        return form if hub.token_ok(form) else None

    def bad_token() -> PlainTextResponse:
        return PlainTextResponse(
            "The form token is missing or wrong. Reload the desk and try again.\n",
            status_code=403,
        )

    def page(name: str, status: int = 200, **context: Any) -> HTMLResponse:
        context.setdefault("signed_in", True)
        context.setdefault("hub", True)
        html = hub.templates.get_template(name).render(token=hub.token, root="", **context)
        return HTMLResponse(html, status_code=status)

    def sign_in_page(error: str = "", email: str = "", status: int = 200) -> HTMLResponse:
        return page(
            "signin.html",
            status=status,
            title="Sign in",
            signed_in=False,
            first=not hub.accounts.has_owner(),
            error=error,
            email=email,
        )

    def with_session(token: str, to: str = "/") -> Response:
        response = _redirect(to)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_SECONDS,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    def refused(exc: AuthError, email: str) -> HTMLResponse:
        response = sign_in_page(error=str(exc), email=email, status=exc.status)
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    def cities_page(
        error: str = "", form: dict[str, str] | None = None, status: int = 200
    ) -> HTMLResponse:
        return page(
            "hub.html",
            status=status,
            title="Cities",
            city_rows=hub.rows(),
            form=form or {},
            error=error,
        )

    @app.get("/sign-in", response_class=HTMLResponse)
    def sign_in_get(request: Request) -> Response:
        if hub.accounts.user_for(request.cookies.get(SESSION_COOKIE)) is not None:
            return _redirect("/")
        return sign_in_page()

    @app.post("/sign-in")
    async def sign_in_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        email = form.get("email", "")
        try:
            token = await run_in_threadpool(
                hub.accounts.sign_in, email, form.get("password", ""), _address(request)
            )
        except AuthError as exc:
            return refused(exc, email)
        return with_session(token)

    @app.post("/sign-up")
    async def sign_up_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        email = form.get("email", "")
        if form.get("password", "") != form.get("confirm", ""):
            return sign_in_page(error="The two passwords differ.", email=email, status=400)
        try:
            token = await run_in_threadpool(
                hub.accounts.create_owner, email, form.get("password", ""), _address(request)
            )
        except AuthError as exc:
            return refused(exc, email)
        # A new owner starts at step 1. "/" would ask /setup, which skips a This computer step
        # that already passes and lands them on Cities (2026-11-25).
        return with_session(token, "/" if hub.slugs() else "/setup/computer")

    @app.post("/sign-out")
    async def sign_out_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        hub.accounts.sign_out(request.cookies.get(SESSION_COOKIE))
        response = _redirect("/sign-in")
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True)
        return response

    @app.get("/static/desk.js")
    def script() -> Response:
        return Response(
            (STATIC_DIR / "desk.js").read_text(encoding="utf-8"), media_type="text/javascript"
        )

    @app.get("/", response_class=HTMLResponse)
    def home() -> Response:
        # A new install has no city yet, so the desk opens on the setup wizard. With at least
        # one city the home page stays the city list, which has its own Set up link.
        if not hub.slugs():
            return _redirect("/setup")
        return cities_page()

    @app.post("/cities/add")
    async def add_city(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        name = form.get("name", "").strip()
        region = form.get("state", "").strip()
        if not name:
            return cities_page(error="Enter the city's name.", form=form, status=400)
        try:
            slug = hub.add(name, region)
        except HubError as exc:
            return cities_page(error=str(exc), form=form, status=400)
        params = {key: value for key, value in (("name", name), ("state", region)) if value}
        target = f"/c/{slug}/sources/find"
        return _redirect(f"{target}?{urlencode(params)}" if params else target)

    # ----- the setup wizard -----------------------------------------------------------------

    def city_specs() -> list[setup_wizard.CitySpec]:
        return [
            setup_wizard.CitySpec(
                slug, hub.cities_dir / slug, setup_wizard.read_setup(hub.cities_dir / slug)
            )
            for slug in hub.slugs()
        ]

    def display_name(spec: setup_wizard.CitySpec) -> str:
        return spec.setup.name or spec.slug

    def setup_page(name: str, step: str, status: int = 200, **context: Any) -> HTMLResponse:
        context["setup_steps"] = [
            {"key": key, "label": label, "number": number, "current": key == step}
            for number, (key, label) in enumerate(setup_wizard.STEPS, start=1)
        ]
        context.setdefault("title", "Set up")
        return page(name, status=status, **context)

    def first_unfinished(checks: list[setup_check.Check]) -> str:
        """The first step whose files (or the PC) do not yet meet its rule."""
        if not setup_wizard.computer_ready(checks, require_signin=False):
            return "computer"
        specs = city_specs()
        if not specs:
            return "cities"
        if any(not setup_wizard.has_sources(spec.city_dir) for spec in specs):
            return "find"
        if any(
            not setup_wizard.check_all_read(setup_wizard.read_proofs(spec.city_dir))
            for spec in specs
        ):
            return "check"
        if any(not setup_wizard.rosters(spec.city_dir) for spec in specs):
            return "rosters"
        return "models-voices"

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_start() -> Response:
        checks = await run_in_threadpool(setup_check.run_checks)
        return _redirect(f"/setup/{first_unfinished(checks)}")

    def computer_step(checks: list[setup_check.Check], notice: str) -> HTMLResponse:
        return setup_page(
            "computer.html",
            "computer",
            title="This computer",
            checks=checks,
            notice=notice,
            setup_next_ready=setup_wizard.computer_ready(checks, require_signin=True),
            setup_blockers=setup_wizard.blocking_rows(checks),
        )

    @app.get("/setup/computer", response_class=HTMLResponse)
    async def setup_computer_get() -> HTMLResponse:
        checks = await run_in_threadpool(setup_check.run_checks)
        return computer_step(checks, "")

    @app.post("/setup/computer")
    async def setup_computer_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        signed_in = form.get("claude") == "1"
        checks = await run_in_threadpool(setup_check.run_checks, claude_signin=signed_in)
        notice = "Claude sign-in checked." if signed_in else "Checked again."
        return computer_step(checks, notice)

    def cities_step(
        text: str = "",
        errors: list[setup_wizard.LineError] | None = None,
        added: list[str] | None = None,
        kept: list[str] | None = None,
        failed: list[str] | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        return setup_page(
            "setup_cities.html",
            "cities",
            status=status,
            title="Cities",
            text=text,
            errors=errors or [],
            added=added or [],
            kept=kept or [],
            failed=failed or [],
            city_rows=hub.rows(),
            setup_next_ready=bool(hub.slugs()),
        )

    @app.get("/setup/cities", response_class=HTMLResponse)
    def setup_cities_get() -> HTMLResponse:
        return cities_step()

    @app.post("/setup/cities")
    async def setup_cities_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        text = form.get("cities", "")
        good, bad_lines = setup_wizard.parse_cities(text)
        added: list[str] = []
        kept: list[str] = []
        failed: list[str] = []
        for city in good:
            folder = hub.cities_dir / slug_from_name(city.name)
            if (folder / "city").is_dir():
                slug = folder.name
                kept.append(city.name)
            else:
                try:
                    slug = hub.add(city.name, city.state)
                except HubError as exc:
                    failed.append(f"{city.name} ({exc})")
                    continue
                added.append(city.name)
            # Keep a website already saved for this city when the typed line leaves it out.
            previous = setup_wizard.read_setup(hub.cities_dir / slug)
            setup_wizard.write_setup(
                hub.cities_dir / slug,
                setup_wizard.CitySetup(city.name, city.state, city.website or previous.website),
            )
        return cities_step(
            text=text,
            errors=bad_lines,
            added=added,
            kept=kept,
            failed=failed,
            status=400 if bad_lines else 200,
        )

    def find_rows() -> list[dict[str, Any]]:
        jobs = hub.setup_jobs
        done = {row.slug: row for row in jobs.status()} if jobs.kind == "find" else {}
        rows: list[dict[str, Any]] = []
        for spec in city_specs():
            row = done.get(spec.slug)
            if row is not None:
                state, message, count = row.state, row.message, row.count
            elif setup_wizard.has_sources(spec.city_dir):
                state, message, count = "saved", "", setup_wizard.source_count(spec.city_dir)
            else:
                state, message, count = "waiting", "", 0
            rows.append(
                {
                    "slug": spec.slug,
                    "name": display_name(spec),
                    "state": state,
                    "message": message,
                    "count": count,
                }
            )
        return rows

    def find_step(status: int = 200) -> HTMLResponse:
        specs = city_specs()
        return setup_page(
            "setup_find.html",
            "find",
            status=status,
            title="Find sources",
            rows=find_rows(),
            running=hub.setup_jobs.running() and hub.setup_jobs.kind == "find",
            setup_next_ready=bool(specs)
            and all(setup_wizard.has_sources(spec.city_dir) for spec in specs),
        )

    @app.get("/setup/find", response_class=HTMLResponse)
    def setup_find_get() -> HTMLResponse:
        return find_step()

    @app.post("/setup/find")
    async def setup_find_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        todo = [spec for spec in city_specs() if not setup_wizard.has_sources(spec.city_dir)]
        hub.setup_jobs.start_find(todo)
        return _redirect("/setup/find")

    def check_step(error: str = "", status: int = 200) -> HTMLResponse:
        specs = city_specs()
        cities = [
            {
                "slug": spec.slug,
                "name": display_name(spec),
                "proofs": setup_wizard.read_proofs(spec.city_dir) or [],
            }
            for spec in specs
        ]
        jobs = hub.setup_jobs
        names = {spec.slug: display_name(spec) for spec in specs}
        job_rows = [
            {
                "slug": row.slug,
                "name": names.get(row.slug, row.slug),
                "state": row.state,
                "message": row.message,
            }
            for row in jobs.status()
        ]
        return setup_page(
            "setup_check.html",
            "check",
            status=status,
            title="Check sources",
            check_cities=cities,
            job_rows=job_rows,
            running=jobs.running() and jobs.kind == "check",
            error=error,
            setup_next_ready=bool(specs)
            and all(
                setup_wizard.check_all_read(setup_wizard.read_proofs(spec.city_dir))
                for spec in specs
            ),
        )

    @app.get("/setup/check", response_class=HTMLResponse)
    def setup_check_get() -> HTMLResponse:
        return check_step()

    @app.post("/setup/check")
    async def setup_check_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        if form.get("action") == "leave-out":
            slug = form.get("slug", "")
            if slug not in hub.slugs():
                return check_step(error=f"No city {slug!r} is here.", status=404)
            if hub.setup_jobs.running():
                return check_step(
                    error="Wait until the current Find or Check finishes, then leave the city out.",
                    status=409,
                )
            left_out = hub.cities_dir / "_left-out"
            left_out.mkdir(exist_ok=True)
            target = left_out / slug
            if target.exists():
                return check_step(error=f"{target} is already there.", status=400)
            desk = hub.desks.pop(slug, None)
            if desk is not None:
                desk.station.stop()
                desk.store.close()
            try:
                (hub.cities_dir / slug).rename(target)
            except OSError as exc:
                # Put the city back so its desk and Station work again.
                with suppress(ReviewError, StationError, OSError, sqlite3.Error):
                    hub.load(slug)
                return check_step(error=f"Could not move {slug}: {exc}", status=500)
            return _redirect("/setup/check")
        hub.setup_jobs.start_check(city_specs())
        return _redirect("/setup/check")

    def rosters_step(error: str = "", status: int = 200) -> HTMLResponse:
        specs = city_specs()
        cities = [
            {
                "slug": spec.slug,
                "name": display_name(spec),
                "rosters": setup_wizard.rosters(spec.city_dir),
                "rows": setup_wizard.roster_body_rows(spec.city_dir),
            }
            for spec in specs
        ]
        jobs = hub.setup_jobs
        names = {spec.slug: display_name(spec) for spec in specs}
        job_rows = [
            {
                "slug": row.slug,
                "name": names.get(row.slug, row.slug),
                "state": row.state,
                "message": row.message,
                "count": row.count,
            }
            for row in jobs.status()
        ]
        return setup_page(
            "setup_rosters.html",
            "rosters",
            status=status,
            title="Rosters",
            roster_cities=cities,
            job_rows=job_rows,
            running=jobs.running() and jobs.kind == "rosters",
            error=error,
            setup_next_ready=bool(cities) and all(city["rosters"] for city in cities),
        )

    @app.get("/setup/rosters", response_class=HTMLResponse)
    def setup_rosters_get() -> HTMLResponse:
        return rosters_step()

    @app.post("/setup/rosters")
    async def setup_rosters_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        hub.setup_jobs.start_rosters(city_specs())
        return _redirect("/setup/rosters")

    def models_step(notice: str = "") -> HTMLResponse:
        specs = city_specs()
        first = specs[0] if specs else None
        city_dir = first.city_dir if first is not None else None
        return setup_page(
            "setup_models_voices.html",
            "models-voices",
            title="Models and voices",
            notice=notice,
            first={"slug": first.slug, "name": display_name(first)} if first else None,
            models=setup_wizard.model_rows(city_dir),
            voice=setup_wizard.voice_summary(city_dir),
        )

    @app.get("/setup/models-voices", response_class=HTMLResponse)
    def setup_models_get() -> HTMLResponse:
        return models_step()

    @app.post("/setup/models-voices")
    async def setup_models_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        specs = city_specs()
        if not specs:
            return models_step("Add a city on the Cities step first.")
        lines = setup_wizard.copy_settings(
            specs[0].city_dir,
            [spec.city_dir for spec in specs[1:]],
            names={spec.city_dir: display_name(spec) for spec in specs},
        )
        notice = " ".join(lines) or "Every other city already has the same files."
        return models_step(notice)

    # ----- the Website step -----------------------------------------------------------------

    def website_defaults() -> dict[str, str]:
        """The form's starting values: the first city's saved site.json, else the patterns."""
        specs = city_specs()
        if specs:
            first = specs[0]
            settings, _ = site_settings.load(first.folder / site_settings.SETTINGS_NAME)
            if settings.target != "none":

                def back(text: str) -> str:
                    return text.replace(first.slug, "{slug}") if first.slug else text

                return {
                    "target": settings.target,
                    "base_url": back(settings.base_url),
                    "media_base_url": back(settings.media_base_url),
                    "out": back(settings.out),
                    "media_out": back(settings.media_out),
                    "project": back(settings.project),
                    "bucket": back(settings.bucket),
                    "prefix": back(settings.prefix),
                    "account": settings.account,
                }
        return {
            "target": "none",
            "base_url": "",
            "media_base_url": "",
            "out": "",
            "media_out": "",
            "project": "civiccast-brief-{slug}",
            "bucket": "",
            "prefix": "{slug}/",
            "account": "",
        }

    def website_rows(values: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for spec in city_specs():
            settings = site_settings.resolve(values, spec.slug)
            if settings.target == "folder":
                fields = [
                    ("Web root", settings.out),
                    ("Media folder", settings.media_out),
                    ("Site address", settings.base_url),
                    ("Media address", settings.media_base_url),
                ]
            elif settings.target == "cloudflare":
                fields = [
                    ("Site address", settings.base_url),
                    ("Pages project", settings.project),
                    ("Bucket", settings.bucket),
                    ("Prefix", settings.prefix),
                    ("Media address", settings.media_base_url),
                    ("Account id", settings.account or "—"),
                ]
            else:
                fields = []
            rows.append(
                {
                    "slug": spec.slug,
                    "name": display_name(spec),
                    "target": settings.target,
                    "fields": fields,
                    "errors": site_settings.validate(settings),
                }
            )
        return rows

    def website_step(
        values: dict[str, str],
        *,
        notice: str = "",
        cloudflare: tuple[bool, bool] | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        return setup_page(
            "setup_website.html",
            "website",
            status=status,
            title="Website",
            form=values,
            rows=website_rows(values),
            notice=notice,
            cloudflare=cloudflare,
            setup_next_ready=bool(city_specs()),
        )

    @app.get("/setup/website", response_class=HTMLResponse)
    def setup_website_get(notice: str = "") -> HTMLResponse:
        text = "Saved the website settings." if notice == "saved" else ""
        return website_step(website_defaults(), notice=text)

    @app.post("/setup/website")
    async def setup_website_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        values = {key: str(form.get(key, "")) for key in website_defaults()}
        if form.get("action") == "check":
            present = await run_in_threadpool(site_settings.credential_exists)
            npx = await run_in_threadpool(site_settings.npx_present)
            return website_step(values, cloudflare=(present, npx))
        specs = city_specs()
        resolved = [(spec, site_settings.resolve(values, spec.slug)) for spec in specs]
        if any(site_settings.validate(settings) for _, settings in resolved):
            return website_step(values, status=400)
        for spec, settings in resolved:
            site_settings.save(spec.folder / site_settings.SETTINGS_NAME, settings)
            desk = hub.desks.get(spec.slug)
            if desk is not None:
                weekly_work = desk.folder / "runs" / "weekly"
                desk.station.set_publish(
                    site_settings.publish_steps(
                        settings, spec.city_dir, desk.store.path, weekly_work
                    )
                )
        return _redirect("/setup/website?notice=saved")

    # ----- the Test step --------------------------------------------------------------------

    def test_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for spec in city_specs():
            state, message, run_id = hub.test_state(spec)
            rows.append(
                {
                    "slug": spec.slug,
                    "name": display_name(spec),
                    "state": state,
                    "message": message,
                    "run_id": run_id,
                }
            )
        return rows

    def test_step(status: int = 200, error: str = "") -> HTMLResponse:
        rows = test_rows()
        return setup_page(
            "setup_test.html",
            "test",
            status=status,
            title="Test",
            rows=rows,
            running=hub.test_jobs.running(),
            error=error,
            setup_next_ready=bool(rows) and all(row["state"] == "ok" for row in rows),
        )

    @app.get("/setup/test", response_class=HTMLResponse)
    def setup_test_get() -> HTMLResponse:
        return test_step()

    @app.post("/setup/test")
    async def setup_test_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        hub.test_jobs.start(city_specs())
        return _redirect("/setup/test")

    # ----- the Every week step --------------------------------------------------------------

    def weekly_defaults() -> dict[str, str]:
        specs = city_specs()
        job = weekly_job.DEFAULT
        if specs:
            try:
                job = weekly_job.load(specs[0].city_dir)
            except weekly_job.WeeklyJobError:
                job = weekly_job.DEFAULT
        values = weekly_job.form_values(job)
        values["diarize"] = "1" if job.diarize else ""
        return values

    def task_status() -> tuple[weekly_task.TaskStatus | None, str]:
        try:
            return weekly_task.status(hub.task_runner), ""
        except weekly_task.WeeklyTaskError as exc:
            return None, str(exc)

    def weekly_rows() -> list[dict[str, Any]]:
        now = datetime.now().astimezone()
        rows: list[dict[str, Any]] = []
        for spec in city_specs():
            row: dict[str, Any] = {
                "slug": spec.slug,
                "name": display_name(spec),
                "enabled": False,
                "next_at": None,
                "problem": "",
            }
            try:
                job = weekly_job.load(spec.city_dir)
            except weekly_job.WeeklyJobError as exc:
                row["problem"] = f"weekly.json could not be read: {exc}"
                rows.append(row)
                continue
            row["enabled"] = job.enabled
            if job.enabled:
                try:
                    row["next_at"] = job.schedule.next_after(now)
                except weekly_job.WeeklyJobError as exc:
                    row["problem"] = str(exc)
            rows.append(row)
        return rows

    def weekly_step(
        form: dict[str, str] | None = None,
        *,
        notice: str = "",
        error: str = "",
        status: int = 200,
        replace_ready: bool = False,
    ) -> HTMLResponse:
        rows = weekly_rows()
        task, task_error = task_status()
        return setup_page(
            "setup_weekly.html",
            "weekly",
            status=status,
            title="Every week",
            form={**weekly_defaults(), **(form or {})},
            rows=rows,
            task=task,
            task_error=task_error,
            other_desk=weekly_task.other_desk(task, hub.cities_dir) if task else "",
            replace_ready=replace_ready,
            notice=notice,
            error=error,
            weekdays=WEEKDAYS,
            setup_next_ready=bool(rows)
            and all(row["enabled"] for row in rows)
            and task is not None
            and task.installed,
        )

    @app.get("/setup/weekly", response_class=HTMLResponse)
    def setup_weekly_get(notice: str = "") -> HTMLResponse:
        return weekly_step(notice=WEEKLY_NOTICES.get(notice, ""))

    @app.post("/setup/weekly")
    async def setup_weekly_post(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        action = form.get("action", "save")
        if action in ("install", "remove"):
            task, _task_error = task_status()
            elsewhere = weekly_task.other_desk(task, hub.cities_dir) if task else ""
            if elsewhere and form.get("replace") != "1":
                if action == "install":
                    error = (
                        f"The Windows task now runs the cities in {elsewhere}. Setting it up "
                        "here replaces it, and those cities stop running every hour. Press the "
                        "button again to replace it."
                    )
                else:
                    error = (
                        f"The Windows task now runs the cities in {elsewhere}. Removing it stops "
                        "those cities running every hour. Press the button again to remove it."
                    )
                return weekly_step(form=dict(form), error=error, status=409, replace_ready=True)
        if action == "install":
            try:
                weekly_task.install(sys.executable, hub.cities_dir, hub.task_runner)
            except weekly_task.WeeklyTaskError as exc:
                return weekly_step(form=dict(form), error=str(exc), status=500)
            return _redirect("/setup/weekly?notice=installed")
        if action == "remove":
            try:
                weekly_task.remove(hub.task_runner)
            except weekly_task.WeeklyTaskError as exc:
                return weekly_step(form=dict(form), error=str(exc), status=500)
            return _redirect("/setup/weekly?notice=removed")
        specs = city_specs()
        if not specs:
            return weekly_step(
                form=dict(form), error="Add a city on the Cities step first.", status=400
            )
        diarize = str(form.get("diarize", "")).strip().lower() in ("1", "on", "true", "yes")
        planned: list[tuple[setup_wizard.CitySpec, weekly_job.WeeklyJob]] = []
        try:
            for spec in specs:
                try:
                    existing = weekly_job.load(spec.city_dir)
                except weekly_job.WeeklyJobError:
                    existing = weekly_job.DEFAULT
                job = weekly_job.WeeklyJob(
                    enabled=True,
                    day=str(form.get("day", "")),
                    at=str(form.get("time", "")),
                    zone=str(form.get("timezone", "")),
                    days=existing.days,
                    diarize=diarize,
                )
                weekly_job.validate(job)
                planned.append((spec, job))
        except weekly_job.WeeklyJobError as exc:
            return weekly_step(form=dict(form), error=f"Not saved: {exc}", status=400)
        for spec, job in planned:
            try:
                weekly_job.save(spec.city_dir, job)
            except (OSError, weekly_job.WeeklyJobError) as exc:
                return weekly_step(form=dict(form), error=f"Not saved: {exc}", status=400)
            desk = hub.desks.get(spec.slug)
            if desk is not None:
                desk.station.reconfigure(
                    steps=weekly_job.job_steps(
                        job, desk.city_dir, desk.store.path, desk.folder / "runs" / "weekly"
                    ),
                    schedule=job.schedule,
                    scheduled=True,
                )
        return _redirect("/setup/weekly?notice=saved")

    # ----- the Everything works step --------------------------------------------------------
    #
    # Every row is read from the files and the dbs at page load, with the same functions the
    # earlier steps use; nothing is stored. A row is OK, or Needs work with one plain sentence
    # and one link to the page that fixes it.

    def done_row(label: str, ok: bool, sentence: str, link: str, link_text: str) -> dict[str, Any]:
        return {
            "label": label,
            "ok": ok,
            "sentence": sentence,
            "link": link,
            "link_text": link_text,
        }

    def sources_row(spec: setup_wizard.CitySpec) -> dict[str, Any]:
        sources_link = f"/c/{spec.slug}/sources"
        count = setup_wizard.source_count(spec.city_dir)
        if count == 0:
            return done_row("Sources", False, "No sources are saved yet.", sources_link, "Sources")
        proofs = setup_wizard.read_proofs(spec.city_dir)
        files = f"{count} source" + ("" if count == 1 else "s")
        if setup_wizard.check_all_read(proofs):
            return done_row("Sources", True, f"{files}, all read.", sources_link, "Sources")
        if proofs is None:
            why = f"{files}, but they are not checked yet."
        else:
            not_read = [
                str(row.get("body") or "a body") for row in proofs if row.get("ok") is not True
            ]
            why = "NOT READ: " + ", ".join(not_read) + "."
        return done_row("Sources", False, why, sources_link, "Sources")

    def rosters_row(spec: setup_wizard.CitySpec) -> dict[str, Any]:
        rosters = setup_wizard.rosters(spec.city_dir)
        missing = [
            str(row["body"])
            for row in setup_wizard.roster_body_rows(spec.city_dir)
            if not row["file"] or row["members"] == 0
        ]
        if missing:
            return done_row(
                "Rosters",
                False,
                "No members for: " + ", ".join(missing) + ".",
                "/setup/rosters",
                "Rosters",
            )
        if not rosters:
            return done_row("Rosters", False, "No roster file yet.", "/setup/rosters", "Rosters")
        members = sum(len(roster.get("members") or []) for roster in rosters)
        files = f"{len(rosters)} roster file" + ("" if len(rosters) == 1 else "s")
        people = f"{members} member" + ("" if members == 1 else "s")
        return done_row("Rosters", True, f"{files}, {people}.", "/setup/rosters", "Rosters")

    def test_brief_row(spec: setup_wizard.CitySpec) -> dict[str, Any]:
        state, message, _ = hub.test_state(spec)
        if state == "ok":
            return done_row(
                "Test brief", True, "A test brief is waiting in Items.", f"/c/{spec.slug}/", "Items"
            )
        if state == "waiting":
            why = "No test run yet."
        else:
            why = message or "The test run did not pass."
        return done_row("Test brief", False, why, "/setup/test", "Test")

    def weekly_row(spec: setup_wizard.CitySpec) -> dict[str, Any]:
        try:
            job = weekly_job.load(spec.city_dir)
        except weekly_job.WeeklyJobError as exc:
            return done_row(
                "Every week",
                False,
                f"weekly.json could not be read: {exc}",
                "/setup/weekly",
                "Every week",
            )
        if not job.enabled:
            return done_row(
                "Every week", False, "The weekly schedule is off.", "/setup/weekly", "Every week"
            )
        try:
            next_at = job.schedule.next_after(datetime.now().astimezone())
        except weekly_job.WeeklyJobError as exc:
            return done_row("Every week", False, str(exc), "/setup/weekly", "Every week")
        when = next_at.strftime("%a %Y-%m-%d %H:%M %Z")
        return done_row(
            "Every week",
            True,
            f"Every {job.day.strip().capitalize()} at {job.at} ({job.zone}); next {when}.",
            "/setup/weekly",
            "Every week",
        )

    def website_row(spec: setup_wizard.CitySpec) -> dict[str, Any]:
        settings, warning = site_settings.load(spec.folder / site_settings.SETTINGS_NAME)
        if warning:
            return done_row("Website", False, warning, "/setup/website", "Website")
        if settings.target == "none":
            return done_row(
                "Website", True, "No website (your choice).", "/setup/website", "Website"
            )
        desk = hub.load(spec.slug)
        _, _, out = site_settings.build_folders(desk.store.path)
        index = out / "index.html"
        if not index.is_file():
            if not desk.store.list_items("published"):
                # publish() only builds the site once a brief is published (weekly/run.py:1047),
                # so this is the desk doing its job, not a failed step.
                return done_row(
                    "Website",
                    True,
                    "The website is built after you publish a brief on the desk.",
                    f"/c/{spec.slug}/",
                    "Items",
                )
            return done_row(
                "Website",
                False,
                "The website is chosen but has not been built yet.",
                "/setup/website",
                "Website",
            )
        built = (
            datetime.fromtimestamp(index.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M")
        )
        if settings.target == "folder" and not (Path(settings.out) / "index.html").is_file():
            return done_row(
                "Website",
                False,
                f"The website was built {built}, but the web root {settings.out} has no index.html.",
                "/setup/website",
                "Website",
            )
        return done_row(
            "Website", True, f"The website was built {built}.", "/setup/website", "Website"
        )

    @app.get("/setup/done", response_class=HTMLResponse)
    async def setup_done_get() -> HTMLResponse:
        checks = await run_in_threadpool(setup_check.run_checks)
        task, task_error = task_status()
        if setup_wizard.computer_ready(checks, require_signin=False):
            computer = done_row(
                "This computer", True, "This computer is ready.", "/setup/computer", "This computer"
            )
        else:
            blockers = ", ".join(setup_wizard.blocking_rows(checks))
            computer = done_row(
                "This computer",
                False,
                f"Still to do: {blockers}.",
                "/setup/computer",
                "This computer",
            )
        if task_error:
            task_row = done_row(
                "Windows task",
                False,
                f"Could not read the Windows task: {task_error}",
                "/setup/weekly",
                "Every week",
            )
        elif task is not None and task.installed:
            elsewhere = weekly_task.other_desk(task, hub.cities_dir)
            if elsewhere:
                task_row = done_row(
                    "Windows task",
                    False,
                    f"The Windows task runs another desk's cities ({elsewhere}), so these "
                    "cities run only while this desk is open.",
                    "/setup/weekly",
                    "Every week",
                )
            else:
                when = (
                    task.next_run.strftime("%a %Y-%m-%d %H:%M %Z")
                    if task.next_run
                    else "not scheduled"
                )
                task_row = done_row(
                    "Windows task",
                    True,
                    f"The Windows task is set up; next run {when}.",
                    "/setup/weekly",
                    "Every week",
                )
        else:
            task_row = done_row(
                "Windows task",
                False,
                "The Windows task is not set up.",
                "/setup/weekly",
                "Every week",
            )
        blocks = [
            {
                "slug": spec.slug,
                "name": display_name(spec),
                "rows": [
                    sources_row(spec),
                    rosters_row(spec),
                    test_brief_row(spec),
                    weekly_row(spec),
                    website_row(spec),
                ],
            }
            for spec in city_specs()
        ]
        rows = [computer, task_row, *(row for block in blocks for row in block["rows"])]
        needs = sum(1 for row in rows if not row["ok"])
        if needs == 0:
            headline = "Everything works."
        else:
            headline = (
                f"{needs} thing"
                + ("" if needs == 1 else "s")
                + (" needs" if needs == 1 else " need")
                + " work."
            )
        return setup_page(
            "setup_done.html",
            "done",
            title="Everything works",
            headline=headline,
            needs=needs,
            global_rows=[computer, task_row],
            blocks=blocks,
        )

    app.mount("/c", CityDispatcher(hub))
    return app
