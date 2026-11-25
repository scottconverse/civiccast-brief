"""The desk's Voices page: choose the engine, its fallback and the two hosts' voices."""

from __future__ import annotations

import json

from test_desk_web import city, client_for, store, token  # noqa: F401

from civiccast_brief.review import voices

FORM = {
    "engine": "gemini",
    "fallback_engine": "lfm3",
    "gemini-voice_s1": "Bodi",
    "gemini-voice_s2": "Puck",
    "lfm3-voice_s1": "male",
    "lfm3-voice_s2": "female",
    "chatterbox-voice_s1": "builtin",
    "chatterbox-voice_s2": "refs\\carter_ref.wav",
}


def test_the_voices_page_shows_the_engines_and_the_code_defaults(store, city):  # noqa: F811
    client = client_for(store, city)
    page = client.get("/voices")
    assert page.status_code == 200 and 'href="/voices"' in page.text  # the nav link
    assert "Google" in page.text and "Runs on this PC" in page.text
    assert page.text.count("Needs the GPU PC") == 4  # Dia2 and Chatterbox, in both selects
    assert 'id="engine"' in page.text and 'id="fallback_engine"' in page.text
    assert '<option value="gemini" selected>' in page.text
    assert '<option value="lfm3" selected>' in page.text  # the fallback
    assert 'value="Bodi"' in page.text and "Code default: Bodi" in page.text
    assert 'data-busy="Saving…"' in page.text
    # Without the script every engine's fields are shown; the script hides the rest.
    for engine in voices.OFFERED:
        assert f'data-voice-engine="{engine}"' in page.text


def test_saving_the_voices_writes_only_what_changed_and_says_what_runs_next(store, city):  # noqa: F811
    client = client_for(store, city)
    saved = client.post("/voices", data={"token": token(client), **FORM})
    assert saved.status_code == 200
    assert "Saved. The next episode uses Gemini, with Bodi and Puck." in saved.text
    data = json.loads((city / "voices.json").read_text(encoding="utf-8"))
    assert data == {
        "engine": "gemini",
        "fallback_engine": "lfm3",
        "audio": {"gemini": {"voice_s2": "Puck"}},  # Bodi is the code default
    }
    again = client.get("/voices").text
    assert '<option value="gemini" selected>' in again and 'value="Puck"' in again


def test_a_bad_voice_keeps_the_form_and_writes_nothing(store, city):  # noqa: F811
    client = client_for(store, city)
    bad = client.post("/voices", data={"token": token(client), **FORM, "gemini-voice_s2": " "})
    assert bad.status_code == 400 and "Not saved" in bad.text and "Enter a voice" in bad.text
    assert 'id="gemini-voice_s2"' in bad.text and 'aria-invalid="true"' in bad.text
    assert not (city / "voices.json").exists()
    assert client.post("/voices", data={**FORM, "token": "wrong"}).status_code == 403


def test_the_same_engine_for_the_fallback_is_refused(store, city):  # noqa: F811
    client = client_for(store, city)
    bad = client.post("/voices", data={"token": token(client), **FORM, "fallback_engine": "gemini"})
    assert bad.status_code == 400 and "different engine" in bad.text
    assert 'id="fallback_engine"' in bad.text and 'aria-invalid="true"' in bad.text
    assert not (city / "voices.json").exists()


def test_the_voices_page_without_a_city_folder_says_so(store):  # noqa: F811
    client = client_for(store)
    page = client.get("/voices")
    assert "started without a city folder" in page.text
    assert 'id="engine"' not in page.text  # no form
    bad = client.post("/voices", data={"token": token(client), **FORM})
    assert bad.status_code == 400 and "--city" in bad.text
