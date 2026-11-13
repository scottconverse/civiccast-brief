import json

import pytest

from civiccast_brief import sources as S

HOST = "reflect-cityofloveland-co.cablecast.tv"


def test_a_cablecast_source_is_checked_normalized_saved_and_loaded(tmp_path):
    source = S.make_source(
        "cablecast", {"host": f"https://{HOST.upper()}/internetchannel", "bodies": "City Council"}
    )
    assert source.kind == "cablecast" and source.host == HOST
    assert source.id == f"cablecast:{HOST}"
    assert source.address == f"https://{HOST}"
    assert S.KIND_LABELS["cablecast"] == "Cablecast video archive"
    S.add_source(tmp_path, source)
    data = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    assert data["sources"] == [
        {"id": f"cablecast:{HOST}", "kind": "cablecast", "host": HOST, "bodies": ["City Council"]}
    ]
    assert S.load_sources(tmp_path) == [source]


def test_the_desk_field_name_is_read_the_same_as_the_file_field():
    desk = S.make_source("cablecast", {"cablecast_host": HOST, "bodies": "City Council"})
    assert desk.host == HOST


@pytest.mark.parametrize(
    "fields,message",
    [
        ({"cablecast_host": ""}, "enter the Cablecast host"),
        ({"cablecast_host": "example.com"}, "not a Cablecast host"),
        ({"cablecast_host": "x.granicus.com"}, "not a Cablecast host"),
        ({"cablecast_host": "evil.cablecast.tv.example.com"}, "not a Cablecast host"),
    ],
)
def test_a_bad_cablecast_host_names_the_field(fields, message):
    with pytest.raises(S.SourceError, match=message) as caught:
        S.make_source("cablecast", {**fields, "bodies": "City Council"})
    assert caught.value.field == "cablecast_host"


def test_the_same_cablecast_host_twice_is_refused(tmp_path):
    source = S.make_source("cablecast", {"host": HOST, "bodies": "City Council"})
    S.add_source(tmp_path, source)
    with pytest.raises(S.SourceError, match="already a source"):
        S.add_source(tmp_path, source)
