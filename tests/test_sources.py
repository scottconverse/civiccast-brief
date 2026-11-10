import json

import pytest

from civiccast_brief import sources as S


def test_each_kind_is_checked_normalized_and_saved(tmp_path):
    pg = S.make_source("primegov", {"url": "longmont.primegov.com/", "bodies": "City Council"})
    yt = S.make_source(
        "youtube",
        {
            "channel_id": "https://www.youtube.com/channel/UCH5_wkpLrKYb1JuUk6-UdNg",
            "bodies": "City Council, Planning and Zoning Commission\nCity Council",
        },
    )
    gr = S.make_source(
        "granicus", {"host": "https://Example.granicus.com/x", "view_id": " 02 ", "bodies": ["A"]}
    )
    assert pg.url == "https://longmont.primegov.com"
    assert yt.channel_id == "UCH5_wkpLrKYb1JuUk6-UdNg"
    assert yt.bodies == ("City Council", "Planning and Zoning Commission")
    assert (gr.host, gr.view_id) == ("example.granicus.com", "2")
    for source in (pg, yt, gr):
        S.add_source(tmp_path, source)
    data = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    assert data["format"] == 1
    assert [row["id"] for row in data["sources"]] == [
        "primegov:longmont.primegov.com",
        "youtube:UCH5_wkpLrKYb1JuUk6-UdNg",
        "granicus:example.granicus.com/2",
    ]
    assert S.load_sources(tmp_path) == [pg, yt, gr]


@pytest.mark.parametrize(
    "kind,fields,field,match",
    [
        ("primegov", {"url": "http://longmont.primegov.com"}, "url", "https://"),
        ("primegov", {"url": "https://longmont.legistar.com"}, "url", "not a PrimeGov portal"),
        ("primegov", {"url": "https://longmont.primegov.com/public/portal"}, "url", "home address"),
        ("primegov", {"url": ""}, "url", "enter the PrimeGov"),
        ("youtube", {"channel_id": "@cityoflongmont"}, "channel_id", "handle"),
        ("youtube", {"channel_id": "UCshort"}, "channel_id", "not a YouTube channel id"),
        ("granicus", {"host": "example.com", "view_id": "2"}, "host", "not a Granicus host"),
        ("granicus", {"host": "x.granicus.com", "view_id": "two"}, "view_id", "is a number"),
        ("granicus", {"host": "x.granicus.com", "view_id": ""}, "view_id", "view id"),
        ("rss", {}, "kind", "pick a kind"),
    ],
)
def test_bad_fields_are_refused_with_the_field_named(kind, fields, field, match):
    with pytest.raises(S.SourceError, match=match) as info:
        S.make_source(kind, {"bodies": "City Council", **fields})
    assert info.value.field == field


def test_a_source_needs_a_body():
    with pytest.raises(S.SourceError, match="at least one body") as info:
        S.make_source("youtube", {"channel_id": "UCH5_wkpLrKYb1JuUk6-UdNg", "bodies": " , \n"})
    assert info.value.field == "bodies"


def test_duplicates_edit_and_remove(tmp_path):
    yt = S.make_source("youtube", {"channel_id": "UCH5_wkpLrKYb1JuUk6-UdNg", "bodies": "A"})
    S.add_source(tmp_path, yt)
    with pytest.raises(S.SourceError, match="already a source"):
        S.add_source(tmp_path, yt)
    other = S.make_source("youtube", {"channel_id": "UCXFW3IRzfCc6q_XM-uutAmw", "bodies": "B"})
    S.replace_source(tmp_path, yt.id, other)
    assert S.load_sources(tmp_path) == [other]
    S.remove_source(tmp_path, other.id)
    assert S.load_sources(tmp_path) == []
    with pytest.raises(S.SourceError, match="no source"):
        S.remove_source(tmp_path, other.id)


def test_a_broken_file_is_reported(tmp_path):
    (tmp_path / "sources.json").write_text('{"sources": [{"kind": "youtube"}]}')
    with pytest.raises(S.SourceError, match="source 1"):
        S.load_sources(tmp_path)


def test_an_empty_form_names_the_address_first():
    with pytest.raises(S.SourceError) as info:
        S.make_source("primegov", {})
    assert info.value.field == "url"
