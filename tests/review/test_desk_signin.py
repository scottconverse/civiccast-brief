"""Desk sign-in, ported from TownReporter: first account owns the desk, then sign-up closes."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from desk_signin import EMAIL, PASSWORD, signed_in
from fastapi.testclient import TestClient

from civiccast_brief.review import auth
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path):
    with ReviewStore(tmp_path / "d.db") as s:
        yield s


def anon(app) -> TestClient:
    return TestClient(app, base_url="https://127.0.0.1")


def form(app, email=EMAIL, password=PASSWORD, **extra) -> dict:
    return {"token": app.state.desk.token, "email": email, "password": password, **extra}


def test_every_desk_page_sends_a_visitor_to_sign_in(store):
    client = anon(make_desk_app(store))
    for path in ("/", "/settings", "/city", "/sources", "/roster", "/items/1"):
        reply = client.get(path, follow_redirects=False)
        assert reply.status_code == 303, path
        assert reply.headers["location"] == "/sign-in"
    post = client.post("/city", data={"token": client.app.state.desk.token})
    assert post.status_code == 401


def test_the_first_account_owns_the_desk_and_then_sign_up_closes(store):
    app = make_desk_app(store)
    client = anon(app)
    first = client.get("/sign-in")
    assert "Create the desk's account" in first.text
    assert 'action="/sign-up"' in first.text
    owner = signed_in(app)
    home = owner.get("/")
    assert home.status_code == 200
    assert "Sign out" in home.text
    page = client.get("/sign-in")
    assert 'action="/sign-in"' in page.text
    assert "Create" not in page.text
    second = client.post(
        "/sign-up",
        data=form(app, "other@example.com", confirm=PASSWORD),
        follow_redirects=False,
    )
    assert second.status_code == 403
    assert "This desk already has an editor. Sign in if that&#39;s you." in second.text
    assert "set-cookie" not in second.headers


def test_sign_in_sets_a_host_prefixed_secure_cookie_and_sign_out_ends_it(store):
    app = make_desk_app(store)
    signed_in(app)
    client = anon(app)
    wrong = client.post("/sign-in", data=form(app, password="not-the-password"))
    assert wrong.status_code == 401
    assert "The email or password is wrong." in wrong.text
    reply = client.post(
        "/sign-in", data=form(app, email=" Editor@Example.COM "), follow_redirects=False
    )
    assert reply.status_code == 303
    cookie = reply.headers["set-cookie"]
    assert cookie.startswith("__Host-ccb.session_token=")
    for part in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert part in cookie
    assert "Domain" not in cookie
    assert client.get("/", follow_redirects=False).status_code == 200
    out = client.post("/sign-out", data={"token": app.state.desk.token}, follow_redirects=False)
    assert out.headers["location"] == "/sign-in"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_the_database_holds_neither_the_session_token_nor_the_password(store, tmp_path):
    app = make_desk_app(store)
    client = signed_in(app)
    token = client.cookies.get(auth.SESSION_COOKIE)
    with sqlite3.connect(tmp_path / "d.db") as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM desk_sessions")]
        password = conn.execute("SELECT password FROM desk_users").fetchone()[0]
    assert token
    assert token not in stored
    assert PASSWORD not in password
    thief = anon(app)
    thief.cookies.set(auth.SESSION_COOKIE, stored[0], domain="127.0.0.1")
    assert thief.get("/", follow_redirects=False).status_code == 303


def test_a_cross_site_request_is_refused_even_with_a_session(store):
    app = make_desk_app(store)
    client = signed_in(app)
    reply = client.post(
        "/sign-out",
        data={"token": app.state.desk.token},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert reply.status_code == 403
    sibling = client.get("/", headers={"sec-fetch-site": "same-site", "sec-fetch-mode": "cors"})
    assert sibling.status_code == 403
    link = client.get("/", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"})
    assert link.status_code == 200


def test_ten_wrong_passwords_lock_the_account_whatever_address_sends_them(tmp_path):
    clock = Clock()
    desk = auth.DeskAuth(tmp_path / "a.db", clock=clock)
    desk.create_owner(EMAIL, PASSWORD, "1.1.1.1")
    for n in range(10):
        with pytest.raises(auth.AuthError) as wrong:
            desk.sign_in(EMAIL, "wrong-password", f"10.0.0.{n}")
        assert wrong.value.status == 401
    with pytest.raises(auth.AuthError) as locked:
        desk.sign_in(EMAIL, PASSWORD, "10.0.1.1")
    assert locked.value.status == 429
    assert str(locked.value) == auth.LOCKED_MESSAGE
    # Knocking while locked does not move the window: it clears 15 minutes after the 10th.
    clock.now += 600
    with pytest.raises(auth.AuthError):
        desk.sign_in(EMAIL, "wrong-password", "10.0.1.2")
    clock.now += 301
    assert desk.user_for(desk.sign_in(EMAIL, PASSWORD, "10.0.1.3")) is not None


def test_an_address_gets_ten_sign_in_tries_per_five_minutes(tmp_path):
    clock = Clock()
    desk = auth.DeskAuth(tmp_path / "a.db", clock=clock)
    desk.create_owner(EMAIL, PASSWORD, "1.1.1.1")
    for n in range(10):
        with pytest.raises(auth.AuthError):
            desk.sign_in(f"nobody{n}@example.com", "wrong-password", "9.9.9.9")
    with pytest.raises(auth.AuthError) as throttled:
        desk.sign_in(EMAIL, PASSWORD, "9.9.9.9")
    assert throttled.value.status == 429
    assert throttled.value.retry_after
    clock.now += 301
    assert desk.sign_in(EMAIL, PASSWORD, "9.9.9.9")


def test_a_session_lasts_seven_days_and_use_renews_it(tmp_path):
    clock = Clock()
    desk = auth.DeskAuth(tmp_path / "a.db", clock=clock)
    token = desk.create_owner(EMAIL, PASSWORD, "1.1.1.1")
    clock.now += 6 * 86400
    assert desk.user_for(token) is not None  # renewed: 7 more days from here
    clock.now += 6 * 86400
    assert desk.user_for(token) is not None
    clock.now += 8 * 86400
    assert desk.user_for(token) is None


def test_the_password_must_be_8_to_128_characters_and_typed_twice_alike(store):
    app = make_desk_app(store)
    client = anon(app)
    short = client.post("/sign-up", data=form(app, password="short", confirm="short"))
    assert short.status_code == 400
    assert "8 to 128 characters" in short.text
    differ = client.post("/sign-up", data=form(app, confirm="something-else"))
    assert differ.status_code == 400
    assert "The two passwords differ." in differ.text
    assert "Create the desk's account" in client.get("/sign-in").text


def test_the_stored_hash_is_better_auth_s_scrypt_format():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password(stored, "correct horse")
    assert not auth.verify_password(stored, "wrong horse")
    # Better Auth: scrypt(NFKC password, salt = the hex salt text, N=16384, r=16, p=1, 64 bytes).
    salt, key = stored.split(":")
    assert len(salt) == 32
    expected = hashlib.scrypt(
        b"correct horse", salt=salt.encode(), n=16384, r=16, p=1, dklen=64, maxmem=2**26
    )
    assert expected.hex() == key
