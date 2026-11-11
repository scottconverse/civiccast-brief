"""The desk pages: guards, item actions, settings and sources, through FastAPI's TestClient."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from civiccast_brief import models, sources
from civiccast_brief.review import desk
from civiccast_brief.review.store import ReviewStore
from civiccast_brief.review.web import make_desk_app


@pytest.fixture
def city(tmp_path):
    folder = tmp_path / "city"
    folder.mkdir()
    return folder


@pytest.fixture
def store(tmp_path):
    with ReviewStore(tmp_path / "d.db") as s:
        yield s


def client_for(store, city=None, **kw):
    return TestClient(make_desk_app(store, city_dir=city, **kw), base_url="http://127.0.0.1")


def token(client) -> str:
    return client.app.state.desk.token


def add_episode(store, meeting_id="m1", body="City Council"):
    return store.add_item(
        meeting_id=meeting_id,
        title="Council <b>night</b>",
        meeting_date="2026-11-04",
        body=body,
        speakers={"voice 01": "unknown"},
        recap="Recap.",
        script="[S1] Script.",
    )


def test_guards_host_token_and_headers(store):
    item = add_episode(store)
    client = client_for(store)
    page = client.get("/")
    assert page.status_code == 200
    assert "Council &lt;b&gt;night&lt;/b&gt;" in page.text  # escaped
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    evil = TestClient(make_desk_app(store), base_url="http://evil.example")
    assert evil.get("/").status_code == 400
    refused = client.post(f"/items/{item.id}/publish", data={"token": "wrong"})
    assert refused.status_code == 403
    assert store.get_item(item.id).status == "review"
    assert client.get("/?status=nope").status_code == 400
    assert client.get("/items/999").status_code == 404


def test_item_buttons_each_end_on_a_page_that_says_what_happened(store):
    item = add_episode(store)
    client = client_for(store)
    t = token(client)

    def press(action, **data):
        response = client.post(f"/items/{item.id}/{action}", data={"token": t, **data})
        assert response.status_code == 200, response.text
        return response.text

    assert "Published at" in press("publish")
    assert "Already published" in press("publish")
    assert "Held." in press("hold")
    assert "Killed." in press("kill")
    refused = client.post(f"/items/{item.id}/publish", data={"token": t})
    assert refused.status_code == 400 and "Not done: a killed item" in refused.text
    saved = press(
        "save",
        recap="Recap, edited.",
        script="[S1] Script.",
        **{"speaker:voice 01": "Clerk"},
    )
    assert "went back to review" in saved
    assert store.get_item(item.id).speakers == {"voice 01": "Clerk"}
    assert "Nothing changed" in press(
        "save", recap="Recap, edited.", script="[S1] Script.", **{"speaker:voice 01": "Clerk"}
    )
    assert "Audio regeneration requested" in press("regenerate")
    assert len(store.list_audio_requests(item.id)) == 1
    no_writer = client.post(f"/items/{item.id}/redraft", data={"token": t, "note": "Shorter."})
    assert no_writer.status_code == 400 and "no writer is connected" in no_writer.text


def test_redraft_uses_the_injected_writer_and_keeps_an_empty_note_out(store):
    item = add_episode(store)
    notes = []

    def writer(current, note):
        notes.append(note)
        return {"recap": "Shorter recap."}

    client = client_for(store, writer=writer)
    t = token(client)
    empty = client.post(f"/items/{item.id}/redraft", data={"token": t, "note": " "})
    assert empty.status_code == 400 and "write a note" in empty.text
    done = client.post(f"/items/{item.id}/redraft", data={"token": t, "note": "Shorter."})
    assert done.status_code == 200 and "Redrafted with your note" in done.text
    assert notes == ["Shorter."] and store.get_item(item.id).recap == "Shorter recap."

    def broken(current, note):
        raise RuntimeError("model timed out")

    failing = client_for(store, writer=broken)
    response = failing.post(
        f"/items/{item.id}/redraft", data={"token": token(failing), "note": "Again."}
    )
    assert response.status_code == 400 and "the writer failed: model timed out" in response.text


def test_an_article_is_edited_on_its_page(store, article):
    item = store.add_item(
        kind="article", meeting_id="m1", title="T", meeting_date="2026-11-04", article=article
    )
    client = client_for(store)
    page = client.get(f"/items/{item.id}")
    assert 'name="s0_p0"' in page.text and "Cites: c1" in page.text
    assert "Regenerate audio" not in page.text
    response = client.post(
        f"/items/{item.id}/save",
        data={
            "token": token(client),
            "headline": "Budget passes",
            "dek": "6-1",
            "s0_heading": "Vote",
            "s0_p0": "It passed\r\n6-1.",
        },
    )
    assert response.status_code == 200
    saved = store.get_item(item.id).article
    assert saved["headline"] == "Budget passes"
    assert saved["sections"][0]["paragraphs"][0] == {"text": "It passed\n6-1.", "cites": ["c1"]}
    bad = client.post(
        f"/items/{item.id}/save", data={"token": token(client), "headline": " ", "s0_p0": "x"}
    )
    assert bad.status_code == 400 and "needs a headline" in bad.text


def test_settings_save_model_picks_and_auto_publish(store, city):
    add_episode(store, body="Planning Commission")
    client = client_for(store, city)
    page = client.get("/settings")
    assert page.status_code == 200 and "Planning Commission" in page.text
    bodies = ["City Council", "Planning Commission"]
    form = {"token": token(client)}
    for task in models.TASKS:
        form[f"{task}::"] = models.DEEPSEEK
        form[f"{task}::0"] = models.CLAUDE
        form[f"{task}::1"] = ""
    form["article::1"] = "claude:claude-sonnet-5-5"
    saved = client.post("/settings/models", data=form)
    assert saved.status_code == 200 and "Saved the model picks" in saved.text
    settings = models.load_settings(city)
    assert settings.pick("article", bodies[1]) == "claude:claude-sonnet-5-5"
    assert settings.pick("recap", bodies[1]) == models.DEEPSEEK
    assert settings.pick("recap", bodies[0]) == models.CLAUDE

    form["recap::"] = "nonsense"
    bad = client.post("/settings/models", data=form)
    assert bad.status_code == 400 and "Not saved" in bad.text
    assert models.load_settings(city).pick("recap", "Library Board") == models.DEEPSEEK

    auto = client.post("/settings/auto-publish", data={"token": token(client), "auto::1": "on"})
    assert auto.status_code == 200 and "It is on for Planning Commission" in auto.text
    assert desk.load_auto_publish(city) == {"City Council": False, "Planning Commission": True}
    off = client.post("/settings/auto-publish", data={"token": token(client)})
    assert off.status_code == 200 and "It is off for every body" in off.text


def test_settings_without_a_city_folder_say_how_to_start(store):
    client = client_for(store)
    assert "--city" in client.get("/settings").text
    response = client.post("/settings/auto-publish", data={"token": token(client)})
    assert response.status_code == 400 and "Start the desk with --city" in response.text


def test_sources_add_edit_remove_and_errors_name_the_field(store, city):
    client = client_for(store, city)
    t = token(client)
    bad = client.post(
        "/sources/add",
        data={"token": t, "kind": "youtube", "channel_id": "@longmont", "bodies": "City Council"},
    )
    assert bad.status_code == 400
    assert 'id="channel_id-error"' in bad.text and "channel handle" in bad.text
    assert 'value="@longmont"' in bad.text  # what was typed is kept
    added = client.post(
        "/sources/add",
        data={
            "token": t,
            "kind": "primegov",
            "url": "https://longmont.primegov.com",
            "bodies": "City Council",
        },
    )
    assert added.status_code == 200
    assert "Added https://longmont.primegov.com for City Council" in added.text
    again = client.post(
        "/sources/add",
        data={
            "token": t,
            "kind": "primegov",
            "url": "longmont.primegov.com",
            "bodies": "City Council",
        },
    )
    assert again.status_code == 400 and "already a source" in again.text

    edit_page = client.get("/sources", params={"edit": "primegov:longmont.primegov.com"})
    assert "Save changes" in edit_page.text
    edited = client.post(
        "/sources/edit",
        data={
            "token": t,
            "source_id": "primegov:longmont.primegov.com",
            "kind": "primegov",
            "url": "https://longmont.primegov.com",
            "bodies": "City Council\nPlanning Commission",
        },
    )
    assert edited.status_code == 200 and "Saved https://longmont.primegov.com" in edited.text
    data = json.loads((city / "sources.json").read_text(encoding="utf-8"))
    assert data["sources"][0]["bodies"] == ["City Council", "Planning Commission"]

    removed = client.post(
        "/sources/remove", data={"token": t, "source_id": "primegov:longmont.primegov.com"}
    )
    assert removed.status_code == 200 and "Removed primegov:longmont.primegov.com" in removed.text
    assert sources.load_sources(city) == []
    gone = client.post(
        "/sources/remove", data={"token": t, "source_id": "primegov:longmont.primegov.com"}
    )
    assert gone.status_code == 400 and "no source" in gone.text
    assert client.get("/sources", params={"edit": "youtube:nope"}).status_code == 404


def test_a_platform_source_opens_for_editing_with_its_tenant_filled_in(store, city):
    sources.add_source(
        city, sources.make_source("civicclerk", {"tenant": "erieco", "bodies": "Town Council"})
    )
    sources.add_source(
        city, sources.make_source("escribe", {"tenant": "bouldercounty", "bodies": "BOCC"})
    )
    client = client_for(store, city)
    clerk = client.get("/sources", params={"edit": "civicclerk:erieco"})
    assert clerk.status_code == 200
    assert 'name="clerk_tenant" value="erieco"' in clerk.text
    county = client.get("/sources", params={"edit": "escribe:bouldercounty"})
    assert 'name="escribe_tenant" value="bouldercounty"' in county.text
    saved = client.post(
        "/sources/edit",
        data={
            "token": token(client),
            "source_id": "civicclerk:erieco",
            "kind": "civicclerk",
            "clerk_tenant": "erieco",
            "bodies": "Town Council\nPlanning Commission",
        },
    )
    assert saved.status_code == 200
    assert sources.load_sources(city)[0].bodies == ("Town Council", "Planning Commission")
