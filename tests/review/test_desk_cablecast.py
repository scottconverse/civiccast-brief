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
