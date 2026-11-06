"""Local producer screen: a WSGI app served on 127.0.0.1 only.

Pages: the item list, and one edit page per item with speaker names, the recap and the script,
plus Save, Approve, Reject and Regenerate audio. Plain HTML forms, no JavaScript. Jinja2
autoescaping is on, so producer text is shown as text.

Two guards keep other pages open in the same browser from using the screen:
  - every POST must carry the form token that the screen prints in its forms. The token is
    random and is made when the app starts. A page on another site cannot read it;
  - the Host header must name 127.0.0.1 or localhost, which stops DNS-rebinding pages.
The screen also refuses to be framed, so a page cannot place its buttons over this one.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Final
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from jinja2 import Environment, FileSystemLoader

from civiccast_brief.review.store import ReviewError, ReviewItem, ReviewStore

logger = logging.getLogger(__name__)

BIND_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
ALLOWED_HOSTNAMES: Final = frozenset({"127.0.0.1", "localhost"})
MAX_FORM_BYTES: Final = 2 * 1024 * 1024
TEMPLATE_DIR: Final = Path(__file__).parent / "templates"

RegenerateCallback = Callable[[ReviewItem], None]

_ACTIONS: Final = frozenset({"save", "approve", "reject", "regenerate"})
_ITEM_PATH: Final = re.compile(r"^/items/(\d+)(?:/([a-z]+))?$")
_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
)
_SECURITY_HEADERS: Final = (
    ("Content-Security-Policy", _CSP),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cache-Control", "no-store"),
)


def local_time(value: str | None) -> str:
    """Show a stored ISO timestamp in local time, e.g. 2026-11-05 23:41 MST."""
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _notice_text(code: str, item: ReviewItem) -> tuple[str, bool] | None:
    """Map a notice code from the query string to (text, is_error). Unknown codes give None."""
    when = local_time(item.approved_at)
    texts = {
        "saved_unchanged": f"Saved. Nothing changed. Status: {item.status}.",
        "saved_draft": "Saved. Status is draft. Approve the item before it can be exported.",
        "saved_reset": "Saved. Status reset to draft. Approve the item again before export.",
        "approved": f"Approved at {when}. The export will include this item.",
        "already_approved": f"Already approved at {when}. Nothing changed.",
        "rejected": "Rejected. The export will skip this item.",
        "regenerate_requested": "Audio regeneration requested. The request is listed below.",
    }
    if code in texts:
        return texts[code], False
    if code == "regenerate_failed":
        return "Audio regeneration failed. The server log has the details.", True
    return None


def _host_allowed(environ: dict) -> bool:
    host = environ.get("HTTP_HOST", "")
    return host.split(":", 1)[0].lower() in ALLOWED_HOSTNAMES


def _read_form(environ: dict) -> dict[str, list[str]] | None:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_FORM_BYTES:
        return None
    body = environ["wsgi.input"].read(length) if length > 0 else b""
    return parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)


def _response(
    status: str,
    body: str,
    content_type: str = "text/html; charset=utf-8",
    extra: Iterable[tuple[str, str]] = (),
) -> tuple[str, list[tuple[str, str]], bytes]:
    headers = [("Content-Type", content_type), *_SECURITY_HEADERS, *extra]
    return status, headers, body.encode("utf-8")


def _plain(status: str, message: str, extra: Iterable[tuple[str, str]] = ()) -> tuple:
    return _response(status, message + "\n", "text/plain; charset=utf-8", extra)


def _redirect(location: str) -> tuple:
    return _response("303 See Other", "", extra=[("Location", location)])


class ReviewApp:
    """The producer screen as a WSGI application."""

    def __init__(self, store: ReviewStore, *, regenerate: RegenerateCallback | None = None):
        self.store = store
        self.token = secrets.token_urlsafe(24)
        self._regenerate = regenerate or self._record_audio_request
        self._templates = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
        self._templates.filters["localtime"] = local_time

    def __call__(self, environ: dict, start_response: Callable) -> list[bytes]:
        status, headers, body = self._respond(environ)
        start_response(status, headers)
        return [body]

    def _respond(self, environ: dict) -> tuple:
        if not _host_allowed(environ):
            return _plain("400 Bad Request", "This screen answers only to 127.0.0.1 or localhost.")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method not in ("GET", "POST"):
            return _plain("405 Method Not Allowed", "Use GET or POST.", [("Allow", "GET, POST")])
        path = environ.get("PATH_INFO") or "/"
        if path == "/":
            if method != "GET":
                return _plain("405 Method Not Allowed", "Use GET.", [("Allow", "GET")])
            return self._list_page()
        match = _ITEM_PATH.match(path)
        if match is None:
            return _plain("404 Not Found", "Not found.")
        item_id = int(match.group(1))
        action = match.group(2)
        if action is None:
            if method != "GET":
                return _plain("405 Method Not Allowed", "Use GET.", [("Allow", "GET")])
            return self._item_page(item_id, environ)
        if action not in _ACTIONS:
            return _plain("404 Not Found", "Not found.")
        if method != "POST":
            return _plain("405 Method Not Allowed", "Use POST.", [("Allow", "POST")])
        return self._action(item_id, action, environ)

    def _list_page(self) -> tuple:
        html = self._templates.get_template("list.html").render(
            title="Meetings", items=self.store.list_items()
        )
        return _response("200 OK", html)

    def _item_page(self, item_id: int, environ: dict) -> tuple:
        item = self.store.get_item(item_id)
        if item is None:
            return _plain("404 Not Found", f"No item with id {item_id}.")
        code = parse_qs(environ.get("QUERY_STRING", "")).get("notice", [""])[0]
        notice = _notice_text(code, item)
        html = self._templates.get_template("item.html").render(
            title=item.meeting_id,
            item=item,
            token=self.token,
            requests=self.store.list_audio_requests(item_id),
            notice=notice[0] if notice else None,
            notice_error=bool(notice and notice[1]),
        )
        return _response("200 OK", html)

    def _action(self, item_id: int, action: str, environ: dict) -> tuple:
        form = _read_form(environ)
        if form is None:
            return _plain("413 Request Entity Too Large", "The form is too large.")
        given = form.get("token", [""])[0]
        if not hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8")):
            return _plain(
                "403 Forbidden",
                "The form token is missing or wrong. Reload the screen and try again.",
            )
        item = self.store.get_item(item_id)
        if item is None:
            return _plain("404 Not Found", f"No item with id {item_id}.")
        try:
            code = self._run_action(action, item, form)
        except ReviewError as exc:
            return _plain("400 Bad Request", str(exc))
        return _redirect(f"/items/{item_id}?notice={code}")

    def _run_action(self, action: str, item: ReviewItem, form: dict[str, list[str]]) -> str:
        if action == "save":
            return self._save(item, form)
        if action == "approve":
            return "approved" if self.store.approve(item.id) else "already_approved"
        if action == "reject":
            self.store.reject(item.id)
            return "rejected"
        try:
            self._regenerate(item)
        except Exception:
            logger.exception("audio regeneration failed for item %d", item.id)
            return "regenerate_failed"
        return "regenerate_requested"

    def _save(self, item: ReviewItem, form: dict[str, list[str]]) -> str:
        speakers = {
            label: form.get(f"speaker:{label}", [name])[0] for label, name in item.speakers.items()
        }
        update = self.store.update_content(
            item.id,
            speakers=speakers,
            recap=form.get("recap", [item.recap])[0],
            script=form.get("script", [item.script])[0],
        )
        if not update.changed:
            return "saved_unchanged"
        if update.previous_status == "draft":
            return "saved_draft"
        return "saved_reset"

    def _record_audio_request(self, item: ReviewItem) -> None:
        self.store.record_audio_request(item.id)


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)


def make_review_server(
    store: ReviewStore,
    port: int = DEFAULT_PORT,
    *,
    regenerate: RegenerateCallback | None = None,
) -> WSGIServer:
    """A server bound to 127.0.0.1. Use port 0 to let the system pick a free port."""
    app = ReviewApp(store, regenerate=regenerate)
    return make_server(BIND_HOST, port, app, handler_class=_QuietHandler)
