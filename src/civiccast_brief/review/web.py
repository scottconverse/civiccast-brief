"""The editor desk: a FastAPI app served on 127.0.0.1 only.

Pages:
  - ``/``: the items, by state (review, held, published, killed);
  - ``/items/<id>``: one episode or article, with Save, Publish, Hold, Kill, Redraft and, for an
    episode, Regenerate audio;
  - ``/settings``: which model writes each task, per body;
  - ``/city``: the city's name, short web name, state and contact emails (city.json).
  - ``/sources``: the city's meeting sources (add, edit, remove).
  - ``/sources/find``: search a city's site and the meeting platforms, then add what was found.

Jinja2 autoescaping is on, so editor text is shown as text. A small script (``/static/desk.js``)
shows "Working..." on the pressed button; every action also ends on a page that says what
happened, so the desk works without the script.

Two guards keep other pages open in the same browser from using the desk:
  - every POST must carry the form token that the desk prints in its forms. The token is random
    and is made when the app starts. A page on another site cannot read it;
  - the Host header must name 127.0.0.1 or localhost, which stops DNS-rebinding pages.
The desk also refuses to be framed, so a page cannot place its buttons over this one.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import logging
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from starlette.concurrency import run_in_threadpool

from civiccast_brief import city as city_file
from civiccast_brief import models as model_settings
from civiccast_brief import sources as city_sources
from civiccast_brief.citybuild.youtube import ChannelError, resolve_channel
from civiccast_brief.review import desk
from civiccast_brief.review.store import STATUSES, DeskItem, ReviewError, ReviewStore
from civiccast_brief.weekly import rosterfill

logger = logging.getLogger(__name__)

BIND_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
ALLOWED_HOSTNAMES: Final = frozenset({"127.0.0.1", "localhost"})
MAX_FORM_BYTES: Final = 2 * 1024 * 1024
HERE: Final = Path(__file__).parent
TEMPLATE_DIR: Final = HERE / "templates"
STATIC_DIR: Final = HERE / "static"

RegenerateCallback = Callable[[DeskItem], None]

_SOURCE_ID: Final = re.compile(
    r"^(primegov|youtube|granicus|cablecast|webpage|gdrive|civicclerk|escribe|civicweb|civicplus|laserfiche):[A-Za-z0-9._/:~%?=&-]{1,500}$"
)
_ITEM_ACTIONS: Final = ("save", "publish", "hold", "kill", "redraft", "regenerate")
_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS: Final = {
    "Content-Security-Policy": _CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
STATE_HELP: Final = {
    "review": "Waiting for the editor.",
    "held": "Set aside. Not published until you publish it.",
    "killed": "Will not run. Edit or redraft it to bring it back to review.",
    "published": "Published. The export writes it.",
}


def local_time(value: str | None) -> str:
    """Show a stored ISO timestamp in local time, e.g. 2026-11-05 23:41 MST."""
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def item_notice(code: str, item: DeskItem) -> tuple[str, bool] | None:
    """Map a notice code from the query string to (text, is_error). Unknown codes give None."""
    when = local_time(item.published_at)
    texts = {
        "saved_unchanged": f"Saved. Nothing changed. State: {item.status}.",
        "saved_review": "Saved. The item is in review. Publish it when it is ready.",
        "saved_reset": "Saved. The item went back to review; publish it again before export.",
        "published": f"Published at {when}. The export will write this item.",
        "already_published": f"Already published at {when}. Nothing changed.",
        "held": "Held. The export skips this item until you publish it.",
        "already_held": "Already held. Nothing changed.",
        "killed": "Killed. The export skips this item. Edit or redraft it to bring it back.",
        "already_killed": "Already killed. Nothing changed.",
        "redrafted": "Redrafted with your note. The new draft is below, in review.",
        "regenerate_requested": "Audio regeneration requested. The request is listed below.",
    }
    if code in texts:
        return texts[code], False
    if code == "regenerate_failed":
        return "Audio regeneration failed. The server log has the details.", True
    return None


def _host_allowed(request: Request) -> bool:
    host = request.headers.get("host", "")
    return host.rsplit(":", 1)[0].lower() in ALLOWED_HOSTNAMES if host else False


def article_from_form(article: Mapping[str, Any], form: Mapping[str, str]) -> dict[str, Any]:
    """The article with the editor's headline, dek, headings and paragraph text from the form."""
    new = json.loads(json.dumps(article))
    new["headline"] = form.get("headline", new.get("headline", ""))
    new["dek"] = form.get("dek", new.get("dek", ""))
    for s_no, section in enumerate(new.get("sections") or []):
        if f"s{s_no}_heading" in form:
            section["heading"] = form[f"s{s_no}_heading"]
        for p_no, paragraph in enumerate(section.get("paragraphs") or []):
            key = f"s{s_no}_p{p_no}"
            if key in form:
                paragraph["text"] = form[key]
    return new


class DeskApp:
    """State shared by the routes: the store, the city folder, the token and the callbacks."""

    def __init__(
        self,
        store: ReviewStore,
        *,
        city_dir: str | Path | None = None,
        writer: desk.Writer | None = None,
        regenerate: RegenerateCallback | None = None,
    ) -> None:
        self.store = store
        self.city_dir = Path(city_dir) if city_dir is not None else None
        self.writer = writer
        self.token = secrets.token_urlsafe(24)
        self._regenerate = regenerate or (lambda item: store.record_audio_request(item.id))
        self.templates = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
        #: Makes the fetcher the city search asks the web through (a test gives its own).
        self.make_fetcher: Callable[[], Any] = _default_fetcher
        #: The last few searches, by id, so Add saves exactly what the editor was shown.
        self.searches: dict[str, Any] = {}
        #: What the last Add from a search did, shown once on the Sources page.
        self.found_lines: list[str] = []
        #: What the last removed source was ("https://... for City Council"), for its notice.
        self.removed: dict[str, str] = {}
        self.templates.filters["localtime"] = local_time
        self.templates.filters["pretty"] = lambda v: json.dumps(v, indent=2, ensure_ascii=False)

    def page(self, name: str, status: int = 200, **context: Any) -> HTMLResponse:
        html = self.templates.get_template(name).render(
            token=self.token, city_dir=self.city_dir, **context
        )
        return HTMLResponse(html, status_code=status)

    def token_ok(self, form: Mapping[str, str]) -> bool:
        given = str(form.get("token", ""))
        return hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))

    # ----- items ----------------------------------------------------------------------------

    def item_page(
        self,
        item: DeskItem,
        *,
        notice: tuple[str, bool] | None = None,
        status: int = 200,
        shown: DeskItem | None = None,
    ) -> HTMLResponse:
        return self.page(
            "item.html",
            status=status,
            title=item.headline or item.meeting_id,
            item=shown or item,
            saved=item,
            state_help=STATE_HELP,
            history=self.store.history(item.id),
            requests=self.store.list_audio_requests(item.id) if item.kind == "episode" else [],
            notice=notice[0] if notice else None,
            notice_error=bool(notice and notice[1]),
            has_writer=self.writer is not None,
        )

    def run_action(self, action: str, item: DeskItem, form: Mapping[str, str]) -> str:
        note = str(form.get("note", "")).strip()
        if action == "save":
            if item.kind == "article":
                update = self.store.update_content(
                    item.id, article=article_from_form(item.article or {}, form)
                )
            else:
                speakers = {
                    label: form.get(f"speaker:{label}", name)
                    for label, name in item.speakers.items()
                }
                update = self.store.update_content(
                    item.id,
                    speakers=speakers,
                    recap=form.get("recap", item.recap),
                    script=form.get("script", item.script),
                )
            if not update.changed:
                return "saved_unchanged"
            return "saved_review" if update.previous_status == "review" else "saved_reset"
        if action == "publish":
            return "published" if self.store.publish(item.id) else "already_published"
        if action == "hold":
            return "held" if self.store.hold(item.id, note) else "already_held"
        if action == "kill":
            return "killed" if self.store.kill(item.id, note) else "already_killed"
        if action == "redraft":
            if self.writer is None:
                raise ReviewError(
                    "no writer is connected to this desk, so it cannot redraft; edit the text by "
                    "hand, or start the desk with --work <the weekly run's work folder>"
                )
            try:
                desk.redraft(self.store, item.id, note, self.writer)
            except ReviewError:
                raise
            except Exception as exc:
                logger.exception("redraft failed for item %d", item.id)
                raise ReviewError(f"the writer failed: {exc}") from exc
            return "redrafted"
        if item.kind != "episode":
            raise ReviewError("only an episode has audio")
        try:
            self._regenerate(item)
        except Exception:
            logger.exception("audio regeneration failed for item %d", item.id)
            return "regenerate_failed"
        return "regenerate_requested"

    def shown_after_error(self, action: str, item: DeskItem, form: Mapping[str, str]) -> DeskItem:
        """What a failed save shows: the editor's text, so nothing typed is lost."""
        if action != "save":
            return item
        if item.kind == "article":
            return dataclasses.replace(item, article=article_from_form(item.article or {}, form))
        return dataclasses.replace(
            item,
            speakers={k: form.get(f"speaker:{k}", v) for k, v in item.speakers.items()},
            recap=form.get("recap", item.recap),
            script=form.get("script", item.script),
        )

    # ----- settings -------------------------------------------------------------------------

    def known_bodies(self) -> list[str]:
        names: set[str] = {i.body for i in self.store.list_items() if i.body}
        if self.city_dir is not None:
            try:
                for source in city_sources.load_sources(self.city_dir):
                    names |= set(source.bodies)
            except city_sources.SourceError:
                pass
            try:
                for picks in model_settings.load_settings(self.city_dir).tasks.values():
                    names |= set(picks.bodies)
            except model_settings.ModelSettingsError:
                pass
        # "City Council" stands in only until the city names its own bodies.
        return sorted(names or {"City Council"}, key=lambda n: (n != "City Council", n.casefold()))

    def settings_page(
        self, *, notice: str = "", error: str = "", status: int = 200
    ) -> HTMLResponse:
        settings = None
        load_error = ""
        if self.city_dir is not None:
            try:
                settings = model_settings.load_settings(self.city_dir)
            except model_settings.ModelSettingsError as exc:
                load_error = str(exc)
        bodies = self.known_bodies()
        picks: dict[str, dict[str, str]] = {}
        choices = list(model_settings.CHOICES)
        if settings is not None:
            for task in model_settings.TASKS:
                task_picks = settings.tasks.get(task) or model_settings.default_picks()
                picks[task] = {"": task_picks.default, **task_picks.bodies}
                choices += [p for p in picks[task].values() if p not in choices]
        return self.page(
            "settings.html",
            status=status,
            title="Settings",
            tasks=model_settings.TASKS,
            bodies=bodies,
            picks=picks,
            choices=choices,
            notice=notice,
            error=error or load_error,
        )

    def save_models(self, form: Mapping[str, str]) -> str:
        assert self.city_dir is not None
        current = model_settings.load_settings(self.city_dir)
        bodies = self.known_bodies()
        for task in model_settings.TASKS:
            default = str(form.get(f"{task}::", "")).strip()
            if not default:
                raise model_settings.ModelSettingsError(f"pick a default model for {task}")
            model_settings.split_pick(default)
            overrides: dict[str, str] = {}
            for number, body in enumerate(bodies):
                pick = str(form.get(f"{task}::{number}", "")).strip()
                if pick:
                    model_settings.split_pick(pick)
                    overrides[body] = pick
            current.tasks[task] = model_settings.TaskPicks(default=default, bodies=overrides)
        path = model_settings.save_settings(self.city_dir, current)
        return f"Saved the model picks to {path}."

    # ----- sources --------------------------------------------------------------------------

    def sources_page(
        self,
        *,
        notice: str = "",
        error: str = "",
        error_field: str = "",
        form: Mapping[str, str] | None = None,
        editing: str = "",
        status: int = 200,
        cablecast_titles: Sequence[tuple[str, int]] = (),
    ) -> HTMLResponse:
        rows: list[city_sources.Source] = []
        load_error = ""
        if self.city_dir is not None:
            try:
                rows = city_sources.load_sources(self.city_dir)
            except city_sources.SourceError as exc:
                load_error = str(exc)
        return self.page(
            "sources.html",
            status=status,
            title="Sources",
            sources=rows,
            kinds=city_sources.KINDS,
            kind_labels=city_sources.KIND_LABELS,
            notice=notice,
            error=error or load_error,
            error_field=error_field,
            form=dict(form or {}),
            editing=editing,
            cablecast_titles=list(cablecast_titles),
            path=(self.city_dir / city_sources.SETTINGS_NAME) if self.city_dir else None,
        )


def _default_fetcher() -> Any:
    from civiccast_brief.citybuild.fetch import Fetcher

    return Fetcher()


#: How many finished searches the desk keeps for their Add button.
MAX_SEARCHES = 5


def _city_names(city_dir: Path | None) -> dict[str, str]:
    """The city's name and state from city.json and roster.json, to start the find form."""
    out: dict[str, str] = {}
    if city_dir is None:
        return out
    for name, keys in (("city.json", ("name",)), ("roster.json", ("name", "state"))):
        try:
            data = json.loads((city_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in keys:
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip() and key not in out:
                out[key] = value.strip()
    return out


def _then_close_chrome(work: Any, *args: Any, **kw: Any) -> Any:
    """Run ``work``, then close the shared Chrome in this same worker thread.

    Playwright objects belong to the thread that made them. Closing Chrome from the server's
    loop thread broke that loop and stopped the desk after a Find search.
    """
    from civiccast_brief.citybuild import browser

    try:
        return work(*args, **kw)
    finally:
        browser.close_shared()


def _redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303)


def make_desk_app(
    store: ReviewStore,
    *,
    city_dir: str | Path | None = None,
    writer: desk.Writer | None = None,
    regenerate: RegenerateCallback | None = None,
) -> FastAPI:
    """The desk. ``writer`` powers Redraft; ``regenerate`` handles Regenerate audio."""
    state = DeskApp(store, city_dir=city_dir, writer=writer, regenerate=regenerate)
    app = FastAPI(
        title="CivicCast Brief editor desk", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.desk = state

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
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    async def read_form(request: Request) -> dict[str, str] | None:
        form = {k: str(v) for k, v in (await request.form()).items()}
        return form if state.token_ok(form) else None

    def bad_token() -> PlainTextResponse:
        return PlainTextResponse(
            "The form token is missing or wrong. Reload the desk and try again.\n",
            status_code=403,
        )

    @app.get("/static/desk.js")
    def script() -> Response:
        return Response(
            (STATIC_DIR / "desk.js").read_text(encoding="utf-8"), media_type="text/javascript"
        )

    @app.get("/", response_class=HTMLResponse)
    def items(status: str = "") -> HTMLResponse:
        if status and status not in STATUSES:
            return state.page(
                "list.html",
                status=400,
                title="Items",
                items=[],
                counts={},
                statuses=STATUSES,
                current="",
                error=f"No state {status!r}. States: {', '.join(STATUSES)}.",
            )
        everything = store.list_items()
        counts = {s: sum(1 for i in everything if i.status == s) for s in STATUSES}
        shown = [i for i in everything if not status or i.status == status]
        return state.page(
            "list.html",
            title="Items",
            items=shown,
            counts=counts,
            statuses=STATUSES,
            current=status,
            error="",
        )

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_page(item_id: int, notice: str = "") -> Response:
        item = store.get_item(item_id)
        if item is None:
            return PlainTextResponse(f"No item with id {item_id}.\n", status_code=404)
        return state.item_page(item, notice=item_notice(notice, item))

    @app.post("/items/{item_id}/{action}")
    async def item_action(item_id: int, action: str, request: Request) -> Response:
        if action not in _ITEM_ACTIONS:
            return PlainTextResponse("Not found.\n", status_code=404)
        form = await read_form(request)
        if form is None:
            return bad_token()
        item = store.get_item(item_id)
        if item is None:
            return PlainTextResponse(f"No item with id {item_id}.\n", status_code=404)
        try:
            code = state.run_action(action, item, form)
        except ReviewError as exc:
            fresh = store.get_item(item_id) or item
            return state.item_page(
                fresh,
                notice=(f"Not done: {exc}", True),
                status=400,
                shown=state.shown_after_error(action, fresh, form),
            )
        return _redirect(f"/items/{item_id}?notice={code}")

    @app.get("/settings", response_class=HTMLResponse)
    def settings(notice: str = "") -> HTMLResponse:
        message = ""
        if state.city_dir is not None and notice == "models":
            where = state.city_dir / model_settings.SETTINGS_NAME
            message = f"Saved the model picks to {where}."
        return state.settings_page(notice=message)

    @app.post("/settings/{which}")
    async def save_settings(which: str, request: Request) -> Response:
        if which != "models":
            return PlainTextResponse("Not found.\n", status_code=404)
        form = await read_form(request)
        if form is None:
            return bad_token()
        if state.city_dir is None:
            return state.settings_page(
                error="Start the desk with --city to save settings.", status=400
            )
        try:
            state.save_models(form)
        except (model_settings.ModelSettingsError, OSError) as exc:
            return state.settings_page(error=f"Not saved: {exc}", status=400)
        return _redirect("/settings?notice=models")

    @app.get("/roster", response_class=HTMLResponse)
    def roster() -> HTMLResponse:
        rosters = rosterfill.loaded_rosters(state.city_dir) if state.city_dir else []
        return state.page("roster.html", title="Roster", rosters=rosters)

    def city_page(
        form: Mapping[str, str] | None = None,
        *,
        notice: str = "",
        error: str = "",
        error_field: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        if form is None and state.city_dir is not None:
            try:
                form = city_file.read_identity(state.city_dir)
            except city_file.CityError as exc:
                error = f"{state.city_dir / city_file.CITY_FILE} cannot be read: {exc}"
        found_platforms: list[str] = []
        if state.city_dir is not None:
            try:
                from civiccast_brief.citybuild.build import video_platforms

                found_platforms = list(video_platforms(city_sources.load_sources(state.city_dir)))
            except city_sources.SourceError:
                pass
        return state.page(
            "city.html",
            status=status,
            title="City",
            platforms=city_file.PLATFORMS,
            found_platforms=found_platforms,
            form=dict(form or {}),
            notice=notice,
            error=error,
            error_field=error_field,
            path=(state.city_dir / city_file.CITY_FILE) if state.city_dir else None,
        )

    @app.get("/city", response_class=HTMLResponse)
    def city(saved: str = "") -> HTMLResponse:
        notice = ""
        if saved and state.city_dir is not None:
            notice = f"Saved to {state.city_dir / city_file.CITY_FILE}."
        return city_page(notice=notice)

    @app.post("/city")
    async def save_city(request: Request) -> Response:
        form = await read_form(request)
        if form is None:
            return bad_token()
        if state.city_dir is None:
            return city_page(form, error="Start the desk with --city to save the city.", status=400)
        try:
            city_file.save_identity(state.city_dir, form)
        except city_file.CityFieldError as exc:
            return city_page(form, error=f"Not saved: {exc}", error_field=exc.field, status=400)
        except (city_file.CityError, OSError) as exc:
            return city_page(form, error=f"Not saved: {exc}", status=400)
        return _redirect("/city?saved=1")

    @app.get("/sources", response_class=HTMLResponse)
    def sources(edit: str = "", done: str = "", id: str = "") -> HTMLResponse:
        form: dict[str, str] = {}
        rows: list[city_sources.Source] = []
        if state.city_dir is not None:
            try:
                rows = city_sources.load_sources(state.city_dir)
            except city_sources.SourceError:
                rows = []
        if edit:
            found = [s for s in rows if s.id == edit]
            if not found:
                return state.sources_page(
                    error=f"No source {edit}; it may have been removed.", status=404
                )
            form = {
                k: (", ".join(v) if k == "bodies" else str(v))
                for k, v in found[0].to_json().items()
                if k != "id"
            }
            if found[0].kind == "webpage":
                form["page_url"] = form.pop("url", "")
            if found[0].kind == "civicclerk":
                form["clerk_tenant"] = form.pop("tenant", "")
            if found[0].kind == "escribe":
                form["escribe_tenant"] = form.pop("tenant", "")
            if found[0].kind == "civicweb":
                form["civicweb_tenant"] = form.pop("tenant", "")
            if found[0].kind == "laserfiche":
                form["laserfiche_url"] = form.pop("url", "")
            if found[0].kind == "cablecast":
                form["cablecast_host"] = form.pop("host", "")
        return state.sources_page(form=form, editing=edit, notice=source_notice(done, id, rows))

    def source_notice(done: str, source_id: str, rows: list[city_sources.Source]) -> str:
        where = state.city_dir / city_sources.SETTINGS_NAME if state.city_dir else ""
        match = next((s for s in rows if s.id == source_id), None)
        if done in ("added", "saved") and match is not None:
            verb = "Added" if done == "added" else "Saved"
            return f"{verb} {match.address} for {', '.join(match.bodies)}. Saved to {where}."
        if done == "found" and state.found_lines:
            lines, state.found_lines = state.found_lines, []
            return " ".join(line.rstrip(".") + "." for line in lines)
        if done == "removed" and match is None and _SOURCE_ID.match(source_id):
            what = state.removed.pop(source_id, "") or source_id
            return f"Removed {what}. Saved to {where}."
        return ""

    @app.get("/sources/find", response_class=HTMLResponse)
    def find_page() -> HTMLResponse:
        return state.page(
            "find.html",
            title="Find sources",
            form=_city_names(state.city_dir),
            found=None,
            error="",
            error_field="",
        )

    @app.post("/sources/find", response_class=HTMLResponse)
    async def find_search(request: Request) -> Response:
        from civiccast_brief.citybuild import browser
        from civiccast_brief.citybuild.cli import parse_also
        from civiccast_brief.citybuild.search import search_city

        form = await read_form(request)
        if form is None:
            return bad_token()

        def again(error: str, error_field: str = "", status: int = 400) -> HTMLResponse:
            return state.page(
                "find.html",
                status=status,
                title="Find sources",
                form=form,
                found=None,
                error=error,
                error_field=error_field,
            )

        if state.city_dir is None:
            return again("Start the desk with --city to find and save sources.")
        name, region = form.get("name", "").strip(), form.get("state", "").strip()
        if not name:
            return again("Enter the city's name.", "name")
        if not region:
            return again("Enter the state, like CO or Colorado.", "state")
        lines = [line for line in form.get("also", "").splitlines() if line.strip()]
        try:
            extra = parse_also(lines)
        except ValueError as exc:
            return again(str(exc).replace("--also ", "The line "), "also")
        fetcher = state.make_fetcher()
        found = await run_in_threadpool(
            _then_close_chrome,
            search_city,
            name,
            region,
            website=form.get("website", "").strip() or None,
            extra=extra,
            fetcher=fetcher,
            open_in_browser=browser.open_page,
        )
        search_id = secrets.token_urlsafe(9)
        state.searches[search_id] = found
        while len(state.searches) > MAX_SEARCHES:
            state.searches.pop(next(iter(state.searches)))
        return state.page(
            "find.html",
            title="Find sources",
            form=form,
            found=found,
            search_id=search_id,
            kind_labels=city_sources.KIND_LABELS,
            error="",
            error_field="",
        )

    @app.post("/sources/find/save")
    async def find_save(request: Request) -> Response:
        from civiccast_brief.citybuild import browser
        from civiccast_brief.citybuild.build import build_city

        raw = await request.form()
        form = {k: str(v) for k, v in raw.items()}
        if not state.token_ok(form):
            return bad_token()
        if state.city_dir is None:
            return state.sources_page(
                error="Start the desk with --city to save sources.", status=400
            )
        found = state.searches.get(form.get("search", ""))
        if found is None:
            return state.page(
                "find.html",
                status=400,
                title="Find sources",
                form=_city_names(state.city_dir),
                found=None,
                error_field="",
                error="That search is no longer kept. Search again, then add.",
            )
        chosen = [str(v) for v in raw.getlist("chosen")]
        if not chosen:
            return state.page(
                "find.html",
                status=400,
                title="Find sources",
                form={},
                found=found,
                search_id=form.get("search", ""),
                error_field="",
                kind_labels=city_sources.KIND_LABELS,
                error="Nothing was added: check at least one source.",
            )
        try:
            built = await run_in_threadpool(
                _then_close_chrome,
                build_city,
                state.city_dir,
                found,
                chosen=chosen,
                fetcher=state.make_fetcher(),
                open_in_browser=browser.open_page,
            )
        except (OSError, city_sources.SourceError) as exc:
            return state.sources_page(error=f"Not saved: {exc}", status=400)
        state.found_lines = built.notice + [
            f"Filled in during the weekly run: {line}" for line in built.to_fill
        ]
        return _redirect("/sources?" + urlencode({"done": "found"}))

    @app.post("/sources/find/add-anyway")
    async def find_add_anyway(request: Request) -> Response:
        """Add a YouTube channel the search spotted but did not offer, the way an offered
        source is added (same writer, same notice)."""
        from civiccast_brief.citybuild import browser
        from civiccast_brief.citybuild.build import build_city

        form = await read_form(request)
        if form is None:
            return bad_token()
        if state.city_dir is None:
            return state.sources_page(
                error="Start the desk with --city to save sources.", status=400
            )
        search_id = form.get("search", "")
        found = state.searches.get(search_id)
        if found is None:
            return state.page(
                "find.html",
                status=400,
                title="Find sources",
                form=_city_names(state.city_dir),
                found=None,
                error_field="",
                error="That search is no longer kept. Search again, then add.",
            )

        def again(error: str) -> HTMLResponse:
            return state.page(
                "find.html",
                status=400,
                title="Find sources",
                form={},
                found=found,
                search_id=search_id,
                error_field="",
                kind_labels=city_sources.KIND_LABELS,
                error=error,
            )

        try:
            unread = found.unread[int(form.get("index", ""))]
        except (ValueError, IndexError):
            return again("That channel is not on the list any more. Search again.")
        if not unread.channel:
            return again("Only a YouTube channel can be added this way.")
        try:
            source = city_sources.make_source(
                "youtube", {"channel_id": unread.channel, "bodies": form.get("bodies", "")}
            )
        except city_sources.SourceError as exc:
            return again(f"Not added: {exc}")
        found.unread.remove(unread)
        found.add(source, f"added anyway ({unread.note})")
        try:
            built = await run_in_threadpool(
                _then_close_chrome,
                build_city,
                state.city_dir,
                found,
                chosen=[source.id],
                fetcher=state.make_fetcher(),
                open_in_browser=browser.open_page,
            )
        except (OSError, city_sources.SourceError) as exc:
            return state.sources_page(error=f"Not saved: {exc}", status=400)
        state.found_lines = built.notice + [f"Still to fill in: {line}" for line in built.to_fill]
        return _redirect("/sources?" + urlencode({"done": "found"}))

    @app.post("/sources/check")
    async def sources_check(request: Request) -> Response:
        """Read each source once, as ``city check`` does, and show READ or NOT READ per body."""
        from civiccast_brief.citybuild.cli import check_city, save_proofs

        form = await read_form(request)
        if form is None:
            return bad_token()
        if state.city_dir is None:
            return state.sources_page(
                error="Start the desk with --city to check sources.", status=400
            )
        source_id = form.get("source_id", "").strip() or None
        try:
            rows = city_sources.load_sources(state.city_dir)
            if source_id is not None and not any(s.id == source_id for s in rows):
                return state.sources_page(
                    error=f"No source {source_id}; it may have been removed.", status=404
                )
            if not rows:
                return state.sources_page(error="There are no sources to check yet.", status=400)
            # Captions stay off, as in the CLI: YouTube's captions are slow and paced.
            proofs = await run_in_threadpool(
                _then_close_chrome,
                check_city,
                state.city_dir,
                fetcher=state.make_fetcher(),
                source_id=source_id,
            )
        except city_sources.SourceError as exc:
            return state.sources_page(error=f"Not checked: {exc}", status=400)
        saved = ""
        if source_id is None:  # one source's result would overwrite the whole city's file
            try:
                saved = str(save_proofs(state.city_dir, proofs))
            except OSError as exc:
                saved = f"not saved: {exc}"
        return state.page(
            "check.html",
            title="Check",
            proofs=proofs,
            kind_labels=city_sources.KIND_LABELS,
            read=sum(p.ok for p in proofs),
            saved=saved,
            one_source=source_id is not None,
        )

    @app.post("/sources/cablecast-titles")
    async def cablecast_titles(request: Request) -> Response:
        """One public read of a Cablecast host's show list, when the user asks for it."""
        from civiccast_brief.citybuild.read import title_body_names
        from civiccast_brief.meeting import cablecast

        form = await read_form(request)
        if form is None:
            return bad_token()
        try:
            host = city_sources.make_source("cablecast", {**form, "bodies": "x"}).host
            shows = await run_in_threadpool(cablecast.list_shows, host)
        except city_sources.SourceError as exc:
            return state.sources_page(
                error=exc.args[0], error_field="cablecast_host", form=form, status=400
            )
        except cablecast.CablecastError as exc:
            return state.sources_page(
                error=f"Could not read the show list: {exc}", form=form, status=400
            )
        names = title_body_names([show.name for show in shows])
        return state.sources_page(
            form=form,
            editing=form.get("source_id", ""),
            cablecast_titles=names,
            notice="" if names else f"{host} lists no shows with video.",
        )

    @app.post("/sources/{action}")
    async def source_action(action: str, request: Request) -> Response:
        if action not in ("add", "edit", "remove"):
            return PlainTextResponse("Not found.\n", status_code=404)
        form = await read_form(request)
        if form is None:
            return bad_token()
        if state.city_dir is None:
            return state.sources_page(
                error="Start the desk with --city to save sources.", status=400
            )
        source_id = form.get("source_id", "")
        try:
            if action == "remove":
                old = next(
                    (s for s in city_sources.load_sources(state.city_dir) if s.id == source_id),
                    None,
                )
                city_sources.remove_source(state.city_dir, source_id)
                if old is not None:
                    state.removed[source_id] = f"{old.address} for {', '.join(old.bodies)}"
                done, new_id = "removed", source_id
            else:
                fields = dict(form)
                typed = form.get("channel_id", "").strip()
                if form.get("kind") == "youtube" and typed:
                    try:
                        fields["channel_id"] = await run_in_threadpool(
                            resolve_channel, typed, state.make_fetcher()
                        )
                    except ChannelError as exc:
                        raise city_sources.SourceError(str(exc), "channel_id") from exc
                source = city_sources.make_source(form.get("kind", ""), fields)
                if action == "add":
                    city_sources.add_source(state.city_dir, source)
                    done = "added"
                else:
                    city_sources.replace_source(state.city_dir, source_id, source)
                    done = "saved"
                new_id = source.id
        except city_sources.SourceError as exc:
            return state.sources_page(
                error=f"Not saved: {exc}",
                error_field=exc.field,
                form=form,
                editing=source_id if action == "edit" else "",
                status=400,
            )
        except OSError as exc:
            return state.sources_page(error=f"Not saved: {exc}", form=form, status=400)
        return _redirect("/sources?" + urlencode({"done": done, "id": new_id}))

    return app


def serve(app: FastAPI, port: int = DEFAULT_PORT) -> None:
    """Run the desk on 127.0.0.1 until Ctrl+C."""
    import uvicorn

    uvicorn.run(app, host=BIND_HOST, port=port, log_level="warning")
