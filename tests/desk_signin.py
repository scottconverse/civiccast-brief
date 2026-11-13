"""A signed-in test client for the editor desk, which needs a signed-in editor on every page.

``tests/`` is on the import path because ``tests/conftest.py`` sits in it with no package file.
The client talks https so the browser-style cookie jar sends the Secure session cookie.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

EMAIL = "editor@example.com"
PASSWORD = "desk-test-password"


def signed_in(app: FastAPI) -> TestClient:
    """A client for ``app``, signed in; it makes the desk's first account when there is none."""
    client = TestClient(app, base_url="https://127.0.0.1")
    first = 'action="/sign-up"' in client.get("/sign-in").text
    data = {"token": app.state.desk.token, "email": EMAIL, "password": PASSWORD}
    if first:
        data["confirm"] = PASSWORD
    reply = client.post("/sign-up" if first else "/sign-in", data=data, follow_redirects=False)
    assert reply.status_code == 303, reply.text
    return client
