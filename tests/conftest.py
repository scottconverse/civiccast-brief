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


class _NoChrome:
    """Stands in for Google Chrome: a test that reaches for the real browser is told no."""

    def __getattr__(self, name: str):
        from civiccast_brief.citybuild.fetch import FetchError

        def refuse(*_args, **_kwargs):
            raise FetchError("no Chrome in tests")

        return refuse


@pytest.fixture(autouse=True)
def no_real_chrome():
    """No test opens the installed Chrome; a test that needs a browser gives a fake one."""
    from civiccast_brief.citybuild import browser

    browser.use(_NoChrome())  # type: ignore[arg-type]
    yield
    browser.use(None)
