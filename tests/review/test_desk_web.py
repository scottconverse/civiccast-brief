"""The desk pages: guards, item actions, settings and sources, through FastAPI's TestClient."""

from __future__ import annotations

import json

import pytest
from desk_signin import signed_in
from fastapi.testclient import TestClient

from civiccast_brief import models, sources
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
    return signed_in(make_desk_app(store, city_dir=city, **kw))


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
        return {"recap": "Shorter recap.", "script": "[S1] Shorter script."}

    client = client_for(store, writer=writer)
    t = token(client)
    empty = client.post(f"/items/{item.id}/redraft", data={"token": t, "note": " "})
    assert empty.status_code == 400 and "write a note" in empty.text
    done = client.post(f"/items/{item.id}/redraft", data={"token": t, "note": "Shorter."})
    assert done.status_code == 200 and "Redrafted with your note" in done.text
    after = store.get_item(item.id)
    # The recap is the run's checked facts and stays; only the script is redrafted.
    assert notes == ["Shorter."] and after.recap == "Recap."
    assert after.script == "[S1] Shorter script."

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


def test_settings_lists_the_city_s_own_bodies_not_a_made_up_city_council(store, city):
    """Loveland has no body named "City Council"; Settings showed one anyway (2026-11-12)."""
    sources.add_source(
        city,
        sources.make_source(
            "civicweb",
            {
                "tenant": "cilovelandco",
                "bodies": "LOVELAND CITY COUNCIL MEETING; Planning Commission",
            },
        ),
    )
    page = client_for(store, city).get("/settings")
    assert "LOVELAND CITY COUNCIL MEETING" in page.text
    assert "City Council</label>" not in page.text
    empty = client_for(store, city=None).get("/settings")
    assert empty.status_code == 200


def test_settings_save_model_picks(store, city):
    add_episode(store)
    add_episode(store, meeting_id="m2", body="Planning Commission")
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

    assert "auto-publish" not in client.get("/settings").text.lower()


def test_settings_without_a_city_folder_say_how_to_start(store):
    client = client_for(store)
    assert "--city" in client.get("/settings").text
    response = client.post("/settings/models", data={"token": token(client)})
    assert response.status_code == 400 and "Start the desk with --city" in response.text
    assert client.post("/settings/auto-publish", data={"token": token(client)}).status_code == 404


def test_sources_add_edit_remove_and_errors_name_the_field(store, city):
    client = client_for(store, city)
    t = token(client)
    bad = client.post(
        "/sources/add",
        data={"token": t, "kind": "youtube", "channel_id": "longmont", "bodies": "City Council"},
    )
    assert bad.status_code == 400
    assert 'id="channel_id-error"' in bad.text and "not a YouTube channel address" in bad.text
    assert 'value="longmont"' in bad.text  # what was typed is kept
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
    listed = client.get("/sources")
    assert (
        'data-confirm="Remove https://longmont.primegov.com for City Council? '
        'The weekly run stops reading it."' in listed.text
    )
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
    # The notice names the source as the page lists it, not by its internal id.
    assert removed.status_code == 200
    assert (
        "Removed https://longmont.primegov.com for City Council, Planning Commission"
        in removed.text
    )
    assert "primegov:longmont.primegov.com" not in removed.text
    assert sources.load_sources(city) == []
    gone = client.post(
        "/sources/remove", data={"token": t, "source_id": "primegov:longmont.primegov.com"}
    )
    assert gone.status_code == 400 and "no source" in gone.text
    assert client.get("/sources", params={"edit": "youtube:nope"}).status_code == 404


def test_editing_a_source_keeps_body_names_that_contain_commas(store, city):
    bodies = "Parks, Open Space, Recreation, and Trails Committee\nPlanning Commission"
    sources.add_source(
        city, sources.make_source("primegov", {"url": "berthoud.primegov.com", "bodies": bodies})
    )
    client = client_for(store, city)
    edit_page = client.get("/sources", params={"edit": "primegov:berthoud.primegov.com"})
    assert bodies in edit_page.text  # the box holds one body per line, not comma-split
    saved = client.post(
        "/sources/edit",
        data={
            "token": token(client),
            "source_id": "primegov:berthoud.primegov.com",
            "kind": "primegov",
            "url": "https://berthoud.primegov.com",
            "bodies": bodies,
        },
    )
    assert saved.status_code == 200 and "Saved https://berthoud.primegov.com" in saved.text
    assert sources.load_sources(city)[0].bodies == (
        "Parks, Open Space, Recreation, and Trails Committee",
        "Planning Commission",
    )


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


def test_a_laserfiche_source_opens_for_editing_with_its_folder_filled_in(store, city):
    url = "https://laserfiche.louisvilleco.gov/WebLink/Browse.aspx?id=8986&dbid=0&repo=Louisville"
    client = client_for(store, city)
    added = client.post(
        "/sources/add",
        data={
            "token": token(client),
            "kind": "laserfiche",
            "laserfiche_url": url,
            "bodies": "Planning Commission",
        },
    )
    assert added.status_code == 200
    assert sources.load_sources(city)[0].url == url
    page = client.get("/sources", params={"edit": f"laserfiche:{url}"})
    assert page.status_code == 200
    assert 'name="laserfiche_url" value="' + url.replace("&", "&amp;") + '"' in page.text


def test_a_civicweb_source_is_added_edited_and_its_tenant_error_names_the_field(store, city):
    client = client_for(store, city)
    t = token(client)
    bad = client.post(
        "/sources/add",
        data={
            "token": t,
            "kind": "civicweb",
            "civicweb_tenant": "https://yourtown.primegov.com",
            "bodies": "City Council",
        },
    )
    assert bad.status_code == 400
    assert 'id="civicweb_tenant-error"' in bad.text
    assert 'placeholder="https://yourtown.civicweb.net"' in bad.text
    added = client.post(
        "/sources/add",
        data={
            "token": t,
            "kind": "civicweb",
            "civicweb_tenant": "https://cilovelandco.civicweb.net/Portal/",
            "bodies": "City Council",
        },
    )
    assert added.status_code == 200
    assert "Added https://cilovelandco.civicweb.net for City Council" in added.text
    assert sources.load_sources(city)[0].id == "civicweb:cilovelandco"
    page = client.get("/sources", params={"edit": "civicweb:cilovelandco"})
    assert page.status_code == 200
    assert 'name="civicweb_tenant" value="cilovelandco"' in page.text
    saved = client.post(
        "/sources/edit",
        data={
            "token": t,
            "source_id": "civicweb:cilovelandco",
            "kind": "civicweb",
            "civicweb_tenant": "cilovelandco",
            "bodies": "City Council\nPlanning Commission",
        },
    )
    assert saved.status_code == 200
    assert sources.load_sources(city)[0].bodies == ("City Council", "Planning Commission")
    removed = client.post(
        "/sources/remove", data={"token": t, "source_id": "civicweb:cilovelandco"}
    )
    assert removed.status_code == 200 and sources.load_sources(city) == []


def test_the_front_page_is_a_control_room_of_problems_and_what_went_out(store):
    out = add_episode(store, "m1")
    store.publish(out.id)
    failed = store.add_item(
        meeting_id="m2",
        title="Planning night",
        meeting_date="2026-11-05",
        checks_passed=False,
        checks_note="a quote is not in the transcript",
        auto_publish=True,
    )
    waiting = add_episode(store, "m3", body="Library Board")
    client = client_for(store)
    page = client.get("/").text
    assert "Control room" in page
    problems = page.split("<h3>Problems (2)</h3>")[1].split("<h3>What went out")[0]
    assert f'href="/items/{failed.id}"' in problems
    assert "Failed its checks: a quote is not in the transcript" in problems
    assert f'href="/items/{waiting.id}"' in problems and "In review" in problems
    went_out = page.split("<h3>What went out (1)</h3>")[1].split("<h2>All items</h2>")[0]
    assert f'action="/items/{out.id}/unpublish"' in went_out
    assert f'action="/items/{out.id}/kill"' in went_out
    assert f'href="/items/{out.id}">Edit</a>' in went_out
    # A status tab shows only its list.
    assert "Control room" not in client.get("/?status=review").text


def test_an_edit_to_a_published_item_stays_published_and_unpublish_takes_it_off(store):
    item = add_episode(store)
    client = client_for(store)
    t = token(client)
    store.publish(item.id)
    page = client.get(f"/items/{item.id}").text
    assert f'action="/items/{item.id}/unpublish"' in page
    assert f'action="/items/{item.id}/publish"' not in page
    assert "Saving a change publishes the change." in page

    saved = client.post(
        f"/items/{item.id}/save",
        data={
            "token": t,
            "recap": "Recap, fixed.",
            "script": "[S1] Script, fixed.",
            "speaker:voice 01": "unknown",
        },
    )
    assert saved.status_code == 200 and "Saved and published again" in saved.text
    after = store.get_item(item.id)
    # The recap comes from the run and is not edited here; the script is.
    assert (after.status, after.recap, after.script) == (
        "published",
        "Recap.",
        "[S1] Script, fixed.",
    )

    off = client.post(f"/items/{item.id}/unpublish", data={"token": t})
    assert off.status_code == 200 and "Unpublished." in off.text
    assert store.get_item(item.id).status == "held"
    again = client.post(f"/items/{item.id}/unpublish", data={"token": t})
    assert again.status_code == 400 and "only a published item can be unpublished" in again.text


def test_a_recap_change_cannot_be_saved_so_the_export_still_matches_the_run(store, tmp_path):
    """The assembler refuses an episode whose exported recap differs from the run's; an edit
    that cannot be saved cannot strand the item, so a save and publish still assemble."""
    from civiccast_brief.review.export import export_published

    item = add_episode(store)
    client = client_for(store)
    store.publish(item.id)
    page = client.get(f"/items/{item.id}").text
    assert 'name="recap"' not in page and "readonly" in page  # shown, not editable

    saved = client.post(
        f"/items/{item.id}/save",
        data={
            "token": token(client),
            "recap": "A recap the run never wrote.",
            "script": "[S1] Script.",
            "speaker:voice 01": "unknown",
        },
    )
    assert saved.status_code == 200
    assert store.get_item(item.id).recap == "Recap."
    out = tmp_path / "export"
    exported = export_published(store.path, out)
    assert len(exported.exported) == 1
    assert (out / "0001-m1" / "recap.txt").read_text(encoding="utf-8") == "Recap."


# ----- the Roster page: members entered by hand, each with its source link ----------------------


def roster_city(city, bodies=("City Council", "Planning Commission")):
    sources.add_source(
        city,
        sources.make_source("primegov", {"url": "longmont.primegov.com", "bodies": list(bodies)}),
    )
    return city


def roster_file(city, body="city-council"):
    return city / "rosters" / f"{body}.json"


def add_member(client, **data):
    form = {"token": token(client), "body": "City Council", "role": "Member"}
    form.update(data)
    return client.post("/roster/add", data=form, follow_redirects=False)


def test_roster_add_writes_the_member_with_its_source_and_hand_mark(store, city):
    roster_city(city)
    client = client_for(store, city)
    page = client.get("/roster")
    assert "Add member" in page.text
    assert "No members yet" in page.text  # City Council and Planning Commission have none

    added = add_member(
        client,
        name="Ada Lovelace",
        role="Mayor",
        source="https://example.org/council#members",
    )
    assert added.status_code == 303
    assert added.headers["location"] == "/roster?notice=added"
    data = json.loads(roster_file(city).read_text(encoding="utf-8"))
    assert data["city"] == ""
    assert (data["body"], data["hand"]) == ("City Council", True)
    assert data["note"] == "Entered on the Roster page; each name's source is in its member entry."
    assert data["sources"] == ["https://example.org/council#members"]
    assert data["members"] == [
        {
            "name": "Ada Lovelace",
            "first": "Ada",
            "last": "Lovelace",
            "role": "Mayor",
            "source": "https://example.org/council#members",
            "entered": data["members"][0]["entered"],
        }
    ]


def test_a_second_add_to_the_same_body_extends_it(store, city):
    roster_city(city)
    client = client_for(store, city)
    add_member(client, name="Ada Lovelace", source="https://example.org/ada")
    add_member(client, name="Grace Hopper", source="https://example.org/grace")
    data = json.loads(roster_file(city).read_text(encoding="utf-8"))
    assert [m["name"] for m in data["members"]] == ["Ada Lovelace", "Grace Hopper"]
    assert data["sources"] == ["https://example.org/ada", "https://example.org/grace"]


@pytest.mark.parametrize("source", ["ftp://example.org/x", ""])
def test_a_bad_source_link_is_refused_and_keeps_what_was_typed(store, city, source):
    roster_city(city)
    client = client_for(store, city)
    response = add_member(client, name="Ada Lovelace", source=source)
    assert response.status_code == 400
    assert "http:// or https://" in response.text
    assert 'value="Ada Lovelace"' in response.text
    assert not roster_file(city).exists()


def test_an_unknown_body_is_refused(store, city):
    roster_city(city)
    client = client_for(store, city)
    response = add_member(client, body="Water Board", name="Ada Lovelace", source="https://x.test")
    assert response.status_code == 400
    assert "not one of this city" in response.text
    assert not roster_file(city).exists()


def test_vacant_is_not_a_person(store, city):
    roster_city(city)
    client = client_for(store, city)
    response = add_member(client, name="Vacant", source="https://x.test")
    assert response.status_code == 400
    assert "Vacant is not a person" in response.text


def test_a_duplicate_name_is_refused(store, city):
    roster_city(city)
    client = client_for(store, city)
    add_member(client, name="Ada Lovelace", source="https://x.test")
    response = add_member(client, name="ada  lovelace", source="https://y.test")
    assert response.status_code == 400
    assert "already on this roster" in response.text
    data = json.loads(roster_file(city).read_text(encoding="utf-8"))
    assert len(data["members"]) == 1


def test_remove_takes_off_a_member_entered_on_the_page(store, city):
    roster_city(city)
    client = client_for(store, city)
    add_member(client, name="Ada Lovelace", source="https://x.test")
    removed = client.post(
        "/roster/remove",
        data={"token": token(client), "body": "City Council", "name": "Ada Lovelace"},
        follow_redirects=False,
    )
    assert removed.status_code == 303
    assert removed.headers["location"] == "/roster?notice=removed"
    assert not roster_file(city).exists()


def test_removing_the_last_of_two_hand_members_keeps_the_one_left(store, city):
    roster_city(city)
    client = client_for(store, city)
    add_member(client, name="Ada Lovelace", source="https://example.org/ada")
    add_member(client, name="Grace Hopper", source="https://example.org/grace")
    removed = client.post(
        "/roster/remove",
        data={"token": token(client), "body": "City Council", "name": "Ada Lovelace"},
        follow_redirects=False,
    )
    assert removed.status_code == 303
    data = json.loads(roster_file(city).read_text(encoding="utf-8"))
    assert data["hand"] is True
    assert [m["name"] for m in data["members"]] == ["Grace Hopper"]


def test_remove_refuses_a_member_the_records_supplied(store, city):
    roster_city(city)
    roster_file(city).parent.mkdir()
    roster_file(city).write_text(
        json.dumps(
            {"body": "City Council", "members": [{"name": "Ada Lovelace", "role": "Member"}]}
        ),
        encoding="utf-8",
    )
    client = client_for(store, city)
    response = client.post(
        "/roster/remove",
        data={"token": token(client), "body": "City Council", "name": "Ada Lovelace"},
    )
    assert response.status_code == 400
    assert "Only names entered on this page can be removed here." in response.text


def test_the_page_shows_a_body_with_no_roster_and_its_entered_names(store, city):
    roster_city(city)
    client = client_for(store, city)
    add_member(client, name="Ada Lovelace", source="https://x.test")
    page = client.get("/roster").text
    assert "Planning Commission" in page and "No members yet" in page
    assert "Ada Lovelace" in page
    assert 'href="https://x.test" rel="noopener noreferrer">open</a>' in page
    assert "data-confirm" in page
    assert "/roster/add" in page


# ----- the Find page's city fields and bodies, and the Voices page's saved fallback -----------


def city_in_slug_folder(tmp_path, *, setup=None, sources_data=None):
    """A city folder laid out as the hub makes it: DIR/<slug>/city, with setup.json beside it."""
    slug = tmp_path / "estes-park"
    city = slug / "city"
    city.mkdir(parents=True)
    if setup is not None:
        (slug / "setup.json").write_text(json.dumps(setup), encoding="utf-8")
    if sources_data is not None:
        (city / "sources.json").write_text(json.dumps(sources_data), encoding="utf-8")
    return city


def search_with(*candidates):
    from civiccast_brief.citybuild.search import Candidate, Search

    return Search(
        name="Longmont",
        state="CO",
        website="https://city.test/",
        candidates=[Candidate(source, "from the site") for source in candidates],
    )


def test_the_find_page_starts_from_setup_json_for_a_new_city(store, tmp_path):
    city = city_in_slug_folder(
        tmp_path,
        setup={"name": "Estes Park", "state": "CO", "website": "https://www.estes.org/"},
    )
    page = client_for(store, city).get("/sources/find")
    assert page.status_code == 200
    assert 'value="Estes Park"' in page.text
    assert 'value="CO"' in page.text
    assert 'value="https://www.estes.org/"' in page.text


def test_the_find_result_keeps_the_city_fields_and_drops_the_searched_also_lines(
    store, city, monkeypatch
):
    from civiccast_brief.citybuild import search as search_module

    found = search_with(
        sources.make_source(
            "webpage", {"url": "https://x.test/agendas", "bodies": ["City Council"]}
        )
    )
    monkeypatch.setattr(search_module, "search_city", lambda *args, **kwargs: found)
    client = client_for(store, city)
    page = client.post(
        "/sources/find",
        data={
            "token": token(client),
            "name": "Longmont",
            "state": "CO",
            "website": "https://x.test/",
            "also": "DDA | https://x.test/dda",
        },
    )
    assert page.status_code == 200
    assert 'value="Longmont"' in page.text and 'value="CO"' in page.text
    assert "DDA | https://x.test/dda" not in page.text
    assert (
        'placeholder="Board of Education | https://www.yourdistrict.org/board/meetings/"></textarea>'
        in page.text
    )


def test_find_save_writes_only_the_ticked_bodies(store, tmp_path):
    url = "https://x.test/agendas"
    city = city_in_slug_folder(
        tmp_path,
        sources_data={
            "format": 1,
            "sources": [{"kind": "webpage", "url": url, "bodies": ["City Council"]}],
        },
    )
    client = client_for(store, city)
    client.app.state.desk.make_fetcher = lambda: None
    source = sources.make_source("webpage", {"url": url, "bodies": ["City Council", "DDA"]})
    client.app.state.desk.searches["s1"] = search_with(source)
    only_council = client.post(
        "/sources/find/save",
        data={
            "token": token(client),
            "search": "s1",
            "chosen": source.id,
            f"body:{source.id}": ["City Council"],
        },
        follow_redirects=False,
    )
    assert only_council.status_code == 303
    assert sources.load_sources(city)[0].bodies == ("City Council",)

    client.app.state.desk.searches["s2"] = search_with(source)
    both = client.post(
        "/sources/find/save",
        data={
            "token": token(client),
            "search": "s2",
            "chosen": source.id,
            f"body:{source.id}": ["City Council", "DDA"],
        },
        follow_redirects=False,
    )
    assert both.status_code == 303
    assert sources.load_sources(city)[0].bodies == ("City Council", "DDA")


def test_find_save_refuses_a_new_source_with_no_body_ticked(store, city):
    client = client_for(store, city)
    client.app.state.desk.make_fetcher = lambda: None
    source = sources.make_source("webpage", {"url": "https://y.test/agendas", "bodies": ["DDA"]})
    client.app.state.desk.searches["s1"] = search_with(source)
    refused = client.post(
        "/sources/find/save",
        data={"token": token(client), "search": "s1", "chosen": source.id},
    )
    assert refused.status_code == 400
    assert "Tick at least one body for https://y.test/agendas." in refused.text
    assert sources.load_sources(city) == []


def test_the_voices_page_shows_the_saved_empty_fallback_as_none(store, city):
    (city / "voices.json").write_text(
        json.dumps({"engine": "gemini", "fallback_engine": "", "audio": {}}), encoding="utf-8"
    )
    page = client_for(store, city).get("/voices")
    assert '<option value="" selected>None</option>' in page.text
    assert '<option value="lfm3" selected>' not in page.text


def test_the_voices_page_starts_on_lfm3_when_there_is_no_voices_json(store, city):
    page = client_for(store, city).get("/voices")
    assert '<option value="lfm3" selected>' in page.text
