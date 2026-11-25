"""The desk's This computer page: the rows, the buttons and the form token."""

from __future__ import annotations

from test_desk_web import city, client_for, store, token  # noqa: F401

from civiccast_brief import setup_check


def rows(*, signin: bool) -> list[setup_check.Check]:
    return [
        setup_check.Check("ffmpeg", "ffmpeg", "required", True, "On PATH.", ""),
        setup_check.Check(
            "claude_signin",
            "Claude sign-in",
            "required",
            True if signin else None,
            "Claude answered." if signin else "Not checked yet.",
            "" if signin else "Press Check Claude sign-in.",
        ),
        setup_check.Check(
            "gemini", "Gemini voice", "voice", False, "No key found.", "Set GEMINI_API_KEY."
        ),
        setup_check.Check("lfm3", "LFM3 voice", "voice", False, "Not found.", "Install it."),
    ]


def test_the_page_shows_every_row_and_the_two_buttons(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(setup_check, "run_checks", lambda **kwargs: rows(signin=False))
    client = client_for(store)
    page = client.get("/setup/computer")
    assert page.status_code == 200
    assert 'href="/setup/computer"' in page.text  # the nav link
    assert "ffmpeg" in page.text and "OK" in page.text
    assert "Not checked yet" in page.text
    assert "Missing" in page.text
    assert "At least one voice engine is needed." in page.text
    assert "Check again" in page.text and "Check Claude sign-in" in page.text
    assert 'data-busy="Checking…"' in page.text
    assert 'name="claude" value="1"' in page.text


def test_check_again_shows_fresh_rows(store, monkeypatch):  # noqa: F811
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return rows(signin=kwargs.get("claude_signin", False))

    monkeypatch.setattr(setup_check, "run_checks", fake_run)
    client = client_for(store)
    client.get("/setup/computer")
    assert calls == [{}]  # the page load checks, without the model

    again = client.post("/setup/computer", data={"token": token(client)})
    assert again.status_code == 200 and "Checked again." in again.text
    assert calls[-1] == {"claude_signin": False}


def test_check_claude_signin_runs_the_signin_probe(store, monkeypatch):  # noqa: F811
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return rows(signin=kwargs["claude_signin"])

    monkeypatch.setattr(setup_check, "run_checks", fake_run)
    client = client_for(store)
    page = client.post("/setup/computer", data={"token": token(client), "claude": "1"})
    assert page.status_code == 200
    assert "Claude sign-in checked." in page.text
    assert "Claude answered." in page.text
    assert calls[-1] == {"claude_signin": True}


def test_a_post_without_the_token_is_refused(store, monkeypatch):  # noqa: F811
    def fake_run(**kwargs):
        raise AssertionError("no probe may run without the token")

    monkeypatch.setattr(setup_check, "run_checks", fake_run)
    client = client_for(store)
    refused = client.post("/setup/computer", data={"claude": "1"})
    assert refused.status_code == 403


def test_the_items_checklist_points_at_this_page(store, city):  # noqa: F811
    client = client_for(store, city)
    page = client.get("/")
    assert "First, check this computer" in page.text
    assert 'href="/setup/computer"' in page.text
