"""The producer screen: escaping, the form guards, POST round trips, and the loopback bind."""

from __future__ import annotations

import html
import re
import threading
import urllib.request
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

import pytest

from civiccast_brief.review.export import export_approved
from civiccast_brief.review.store import ReviewItem, ReviewStore
from civiccast_brief.review.web import ReviewApp, local_time, make_review_server

SPEAKERS = {"SPEAKER_00": "Mayor Smith", "SPEAKER_01": "unknown"}
RECAP = "The council voted five to two."
SCRIPT = "[S1] Five to two.\n[S2] Right."


def call(
    app: ReviewApp,
    method: str,
    path: str,
    *,
    form: dict[str, str] | None = None,
    host: str = "127.0.0.1:8765",
    query: str = "",
) -> tuple[str, dict[str, str], str]:
    """Call the WSGI app directly and return (status, headers, body)."""
    body = urlencode(form, doseq=True).encode("utf-8") if form is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "HTTP_HOST": host,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": BytesIO(body),
        "SERVER_NAME": "127.0.0.1",
        "SERVER_PORT": "8765",
        "wsgi.url_scheme": "http",
    }
    seen: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        seen["status"] = status
        seen["headers"] = dict(headers)

    text = b"".join(app(environ, start_response)).decode("utf-8")
    return str(seen["status"]), dict(seen["headers"]), text  # type: ignore[arg-type]


def textarea_value(page: str, name: str) -> str:
    """The value a browser would submit for a textarea: the first newline after the tag is dropped."""
    match = re.search(rf'<textarea name="{name}">(.*?)</textarea>', page, re.DOTALL)
    assert match, f"no textarea {name!r} on the page"
    return html.unescape(match.group(1)).removeprefix("\n")


def form_for(app: ReviewApp, item: ReviewItem, **overrides: str) -> dict[str, str]:
    form = {"token": app.token, "recap": item.recap, "script": item.script}
    for label, name in item.speakers.items():
        form[f"speaker:{label}"] = name
    form.update(overrides)
    return form


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    return ReviewStore(tmp_path / "review.db")


@pytest.fixture
def item(store: ReviewStore) -> ReviewItem:
    return store.add_item(
        meeting_id="2026-11-04-longmont-council",
        title="Longmont City Council, November 4",
        meeting_date="2026-11-04",
        speakers=SPEAKERS,
        recap=RECAP,
        script=SCRIPT,
    )


@pytest.fixture
def app(store: ReviewStore) -> ReviewApp:
    return ReviewApp(store)


def test_list_and_item_pages_render(app: ReviewApp, item: ReviewItem) -> None:
    status, headers, page = call(app, "GET", "/")
    assert status == "200 OK"
    assert item.title in page
    assert headers["Content-Type"].startswith("text/html")

    status, _, page = call(app, "GET", f"/items/{item.id}")
    assert status == "200 OK"
    assert RECAP in page
    assert "Approve" in page and "Reject" in page and "Regenerate audio" in page


def test_producer_text_is_escaped(app: ReviewApp, store: ReviewStore) -> None:
    hostile = store.add_item(
        meeting_id="m-hostile",
        title="<script>alert('title')</script>",
        meeting_date="2026-11-04",
        speakers={"SPEAKER_00": "<b>Mayor</b>"},
        recap="<script>alert('recap')</script></textarea><img src=x onerror=alert(1)>",
        script="<script>alert('script')</script>",
    )

    _, _, listing = call(app, "GET", "/")
    _, _, page = call(app, "GET", f"/items/{hostile.id}")

    for body in (listing, page):
        assert "<script>alert" not in body
        assert "</textarea><img" not in body
        assert "<img src=x" not in body
    assert "&lt;script&gt;alert(&#39;recap&#39;)" in page
    assert "&lt;b&gt;Mayor" in page
    assert "&lt;script&gt;alert(&#39;title&#39;)" in listing


def test_pages_refuse_to_be_framed(app: ReviewApp, item: ReviewItem) -> None:
    _, headers, _ = call(app, "GET", f"/items/{item.id}")

    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_post_without_the_form_token_changes_nothing(app: ReviewApp, item: ReviewItem) -> None:
    status, _, _ = call(app, "POST", f"/items/{item.id}/approve", form={})

    assert status == "403 Forbidden"
    assert app.store.get_item(item.id).status == "draft"


def test_post_with_a_wrong_token_changes_nothing(app: ReviewApp, item: ReviewItem) -> None:
    status, _, _ = call(app, "POST", f"/items/{item.id}/approve", form={"token": "guessed-token"})

    assert status == "403 Forbidden"
    assert app.store.get_item(item.id).status == "draft"


def test_other_host_names_are_refused(app: ReviewApp, item: ReviewItem) -> None:
    status, _, _ = call(app, "GET", f"/items/{item.id}", host="evil.example:8765")
    assert status == "400 Bad Request"

    status, _, _ = call(
        app,
        "POST",
        f"/items/{item.id}/approve",
        form={"token": app.token},
        host="evil.example:8765",
    )
    assert status == "400 Bad Request"
    assert app.store.get_item(item.id).status == "draft"

    for host in ("127.0.0.1:8765", "localhost:8765", "localhost"):
        status, _, _ = call(app, "GET", "/", host=host)
        assert status == "200 OK", host


def test_approve_by_post_then_export_succeeds(
    app: ReviewApp, item: ReviewItem, tmp_path: Path
) -> None:
    status, headers, _ = call(app, "POST", f"/items/{item.id}/approve", form={"token": app.token})
    assert status == "303 See Other"
    assert headers["Location"] == f"/items/{item.id}?notice=approved"

    _, _, page = call(app, "GET", f"/items/{item.id}", query="notice=approved")
    assert "Approved at" in page
    assert "The export will include this item." in page

    result = export_approved(app.store.path, tmp_path / "export")
    assert result.skipped == ()
    assert len(result.exported) == 1


def test_save_by_post_resets_an_approved_item_to_draft(
    app: ReviewApp, item: ReviewItem, tmp_path: Path
) -> None:
    app.store.approve(item.id)
    form = form_for(app, item, **{"speaker:SPEAKER_00": "Mayor Jones"})
    form["recap"] = "A corrected recap."

    status, headers, _ = call(app, "POST", f"/items/{item.id}/save", form=form)

    assert status == "303 See Other"
    assert headers["Location"] == f"/items/{item.id}?notice=saved_reset"
    _, _, page = call(app, "GET", f"/items/{item.id}", query="notice=saved_reset")
    assert "Status reset to draft" in page
    after = app.store.get_item(item.id)
    assert after.status == "draft"
    assert after.speakers["SPEAKER_00"] == "Mayor Jones"
    assert after.recap == "A corrected recap."
    assert export_approved(app.store.path, tmp_path / "export").exported == ()


def test_saving_the_page_unchanged_keeps_the_approval(app: ReviewApp, store: ReviewStore) -> None:
    # A leading newline in the recap is the case that a browser round trip could change.
    item = store.add_item(
        meeting_id="m-newline",
        title="Newline test",
        meeting_date="2026-11-04",
        speakers=SPEAKERS,
        recap="\nLeading line.\nSecond line.",
        script=SCRIPT,
    )
    store.approve(item.id)
    _, _, page = call(app, "GET", f"/items/{item.id}")
    browser_recap = textarea_value(page, "recap").replace("\n", "\r\n")
    browser_script = textarea_value(page, "script").replace("\n", "\r\n")
    form = form_for(app, item, recap=browser_recap, script=browser_script)

    status, headers, _ = call(app, "POST", f"/items/{item.id}/save", form=form)

    assert status == "303 See Other"
    assert headers["Location"] == f"/items/{item.id}?notice=saved_unchanged"
    assert store.get_item(item.id).status == "approved"


def test_regenerate_default_records_a_request(app: ReviewApp, item: ReviewItem) -> None:
    status, headers, _ = call(
        app, "POST", f"/items/{item.id}/regenerate", form={"token": app.token}
    )

    assert status == "303 See Other"
    assert headers["Location"] == f"/items/{item.id}?notice=regenerate_requested"
    requests = app.store.list_audio_requests(item.id)
    assert len(requests) == 1
    _, _, page = call(app, "GET", f"/items/{item.id}", query="notice=regenerate_requested")
    assert "Audio regeneration requested" in page
    assert "No audio requests yet" not in page
    assert local_time(requests[0].requested_at) in page


def test_regenerate_uses_the_pluggable_callback(store: ReviewStore, item: ReviewItem) -> None:
    calls: list[int] = []
    app = ReviewApp(store, regenerate=lambda it: calls.append(it.id))

    call(app, "POST", f"/items/{item.id}/regenerate", form={"token": app.token})

    assert calls == [item.id]
    assert store.list_audio_requests(item.id) == []


def test_regenerate_failure_is_shown_to_the_producer(store: ReviewStore, item: ReviewItem) -> None:
    def broken(_: ReviewItem) -> None:
        raise RuntimeError("engine offline")

    app = ReviewApp(store, regenerate=broken)
    status, headers, _ = call(
        app, "POST", f"/items/{item.id}/regenerate", form={"token": app.token}
    )
    assert status == "303 See Other"
    assert headers["Location"] == f"/items/{item.id}?notice=regenerate_failed"

    _, _, page = call(app, "GET", f"/items/{item.id}", query="notice=regenerate_failed")
    assert "Audio regeneration failed" in page
    assert "engine offline" not in page


def test_unknown_items_routes_and_methods(app: ReviewApp, item: ReviewItem) -> None:
    assert call(app, "GET", "/items/999")[0] == "404 Not Found"
    assert call(app, "POST", "/items/999/approve", form={"token": app.token})[0] == (
        "404 Not Found"
    )
    assert call(app, "GET", f"/items/{item.id}/nonsense")[0] == "404 Not Found"
    assert call(app, "PUT", "/")[0] == "405 Method Not Allowed"
    assert call(app, "GET", f"/items/{item.id}/approve")[0] == "405 Method Not Allowed"


def test_server_binds_to_loopback_only(store: ReviewStore) -> None:
    server = make_review_server(store, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_real_socket_round_trip_approves_and_exports(
    store: ReviewStore, item: ReviewItem, tmp_path: Path
) -> None:
    server = make_review_server(store, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    # No proxies: a system proxy must not carry loopback requests off the machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{base}/items/{item.id}", timeout=10) as page:
            page_text = page.read().decode("utf-8")
        token = re.search(r'name="token" value="([^"]+)"', page_text)
        assert token is not None
        data = urlencode({"token": token.group(1)}).encode("utf-8")
        request = urllib.request.Request(f"{base}/items/{item.id}/approve", data=data)
        with opener.open(request, timeout=10) as reply:
            reply_text = reply.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "Approved at" in reply_text
    assert store.get_item(item.id).status == "approved"
    assert len(export_approved(store.path, tmp_path / "export").exported) == 1
