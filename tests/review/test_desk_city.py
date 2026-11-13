"""The desk's City page reads and saves the city's name, short web name, state and emails."""

import json

from test_desk_web import city, client_for, store, token  # noqa: F401

FULL = {
    "slug": "oldname",
    "name": "Old Name",
    "site_title": "CivicCast Brief: Old Name",
    "hosts": {"S1": "Maya", "S2": "Theo"},
    "about": ["Kept."],
    "contact_email": "old@example.org",
    "capture": {"platform": "granicus", "host": "x.granicus.com", "view_id": 2},
    "podcast": {"name": "Show", "owner_email": "pod@example.org", "category": "Government"},
    "signup_url": "https://example.org/join",
}
GOOD = {
    "name": "Loveland",
    "slug": "loveland",
    "state": "CO",
    "contact_email": "corrections@example.org",
    "podcast_email": "podcast@example.org",
}


def test_the_city_page_shows_what_city_json_holds_and_saves_only_those_fields(store, city):  # noqa: F811
    path = city / "city.json"
    path.write_text(json.dumps(FULL), encoding="utf-8")
    client = client_for(store, city)
    page = client.get("/city")
    assert page.status_code == 200 and 'href="/city"' in page.text
    assert 'value="Old Name"' in page.text and 'value="pod@example.org"' in page.text
    assert 'data-busy="Saving the city' in page.text

    done = client.post("/city", data={"token": token(client), **GOOD})
    assert done.status_code == 200
    assert f"Saved to {path}" in done.text  # the visible result
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["name"] == "Loveland" and saved["slug"] == "loveland"
    assert saved["state"] == "Colorado"  # a code is stored as the full name
    assert saved["contact_email"] == "corrections@example.org"
    assert saved["podcast"]["owner_email"] == "podcast@example.org"
    expected = {**FULL, **{k: saved[k] for k in ("name", "slug", "state", "contact_email")}}
    expected["podcast"] = {**FULL["podcast"], "owner_email": "podcast@example.org"}
    assert saved == expected  # every other field, and their order of nesting, untouched
    assert 'value="Colorado"' in client.get("/city").text


def test_the_city_page_names_each_bad_value_and_keeps_what_was_typed(store, city):  # noqa: F811
    path = city / "city.json"
    path.write_text(json.dumps(FULL), encoding="utf-8")
    client = client_for(store, city)
    cases = [
        ({"name": " "}, "name", "Enter the city"),
        ({"slug": "Lovel and!"}, "slug", "short web name"),
        ({"state": "Atlantis"}, "state", "Enter a US state"),
        ({"contact_email": "nobody"}, "contact_email", "contact email"),
        ({"podcast_email": ""}, "podcast_email", "podcast email"),
    ]
    for change, field, message in cases:
        bad = client.post("/city", data={"token": token(client), **GOOD, **change})
        assert bad.status_code == 400, field
        assert "Not saved" in bad.text and message in bad.text
        assert f'id="{field}"' in bad.text and 'aria-invalid="true"' in bad.text
    assert json.loads(path.read_text(encoding="utf-8")) == FULL  # nothing was written
    typed = client.post("/city", data={"token": token(client), **GOOD, "slug": "BAD slug"})
    assert 'value="BAD slug"' in typed.text and 'value="Loveland"' in typed.text
    assert client.post("/city", data={**GOOD, "token": "wrong"}).status_code == 403


def test_saving_the_city_makes_city_json_when_the_folder_has_none(store, city):  # noqa: F811
    client = client_for(store, city)
    page = client.get("/city")
    assert page.status_code == 200 and 'name="slug"' in page.text
    assert client.post("/city", data={"token": token(client), **GOOD}).status_code == 200
    made = json.loads((city / "city.json").read_text(encoding="utf-8"))
    assert made["slug"] == "loveland" and made["capture"] == {"platform": "youtube"}
    assert made["podcast"]["owner_email"] == "podcast@example.org"


def test_the_city_page_without_a_city_folder_says_so(store):  # noqa: F811
    client = client_for(store)
    assert "started without a city folder" in client.get("/city").text
    bad = client.post("/city", data={"token": token(client), **GOOD})
    assert bad.status_code == 400 and "--city" in bad.text
