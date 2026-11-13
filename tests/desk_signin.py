"""A signed-in test client for the editor desk, which needs a signed-in editor on every page.

``tests/`` is on the import path because ``tests/conftest.py`` sits in it with no package file.
The client talks https so the browser-style cookie jar sends the Secure session cookie.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

EMAIL = "editor@example.com"
PASSWORD = "desk-test-password"


def signed_in(app: FastAPI, root: str = "") -> TestClient:
    """A client for ``app``, signed in; it makes the desk's first account when there is none.

    ``root`` is where the desk is mounted ("/desk" in the station).
    """
    client = TestClient(app, base_url="https://127.0.0.1")
    first = f'action="{root}/sign-up"' in client.get(f"{root}/sign-in").text
    data = {"token": app.state.desk.token, "email": EMAIL, "password": PASSWORD}
    if first:
        data["confirm"] = PASSWORD
    path = f"{root}/sign-up" if first else f"{root}/sign-in"
    reply = client.post(path, data=data, follow_redirects=False)
    assert reply.status_code == 303, reply.text
    return client
