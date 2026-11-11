"""Settings shared by every test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_real_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads the machine's real YouTube key.

    A developer's machine can hold a real key in Credential Manager or the environment. Every
    test starts with neither; a test that wants a key sets a fake one itself.
    """
    from civiccast_brief.record import credentials

    monkeypatch.delenv(credentials.YOUTUBE_API_KEY_ENV, raising=False)
    monkeypatch.setattr(credentials, "read_credential", lambda target: None)
