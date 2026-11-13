"""The desk's Sources page takes a Cablecast source, keeps what was typed and opens it to edit."""

from test_desk_web import city, client_for, store, token  # noqa: F401

from civiccast_brief import sources

HOST = "reflect-cityofloveland-co.cablecast.tv"


def test_a_cablecast_source_is_added_then_opened_for_editing(store, city):  # noqa: F811
    client = client_for(store, city)
    bad = client.post(
        "/sources/add",
        data={
            "token": token(client),
            "kind": "cablecast",
            "cablecast_host": "example.com",
            "bodies": "City Council",
        },
    )
    assert bad.status_code == 400
    assert 'id="cablecast_host-error"' in bad.text and "not a Cablecast host" in bad.text
    assert 'value="example.com"' in bad.text

    added = client.post(
        "/sources/add",
        data={
            "token": token(client),
            "kind": "cablecast",
            "cablecast_host": HOST,
            "bodies": "City Council",
        },
    )
    assert added.status_code == 200
    assert f"Added https://{HOST} for City Council" in added.text
    assert [s.id for s in sources.load_sources(city)] == [f"cablecast:{HOST}"]
    assert "Cablecast video archive" in added.text

    edit = client.get("/sources", params={"edit": f"cablecast:{HOST}"})
    assert edit.status_code == 200
    assert f'name="cablecast_host" value="{HOST}"' in edit.text


def test_show_recent_titles_lists_the_body_names_from_one_read(store, city, monkeypatch):  # noqa: F811
    import json
    from pathlib import Path

    from civiccast_brief.meeting import cablecast

    fixtures = Path(__file__).parent.parent / "meeting" / "fixtures" / "cablecast"
    shows = cablecast.parse_shows(json.loads((fixtures / "shows.json").read_text("utf-8")), HOST)
    asked: list[str] = []
    monkeypatch.setattr(cablecast, "list_shows", lambda host: asked.append(host) or shows)
    client = client_for(store, city)
    page = client.get("/sources")
    assert "Show recent titles" in page.text and asked == []  # nothing is read until asked
    reply = client.post(
        "/sources/cablecast-titles",
        data={"token": token(client), "kind": "cablecast", "cablecast_host": HOST, "bodies": "x"},
    )
    assert reply.status_code == 200 and asked == [HOST]
    assert "City Council Meeting (" in reply.text and "Planning Commission (" in reply.text
    assert f'name="cablecast_host" value="{HOST}"' in reply.text
    assert sources.load_sources(city) == []  # nothing was saved
    bad = client.post(
        "/sources/cablecast-titles",
        data={"token": token(client), "kind": "cablecast", "cablecast_host": "example.com"},
    )
    assert bad.status_code == 400 and "not a Cablecast host" in bad.text and asked == [HOST]
