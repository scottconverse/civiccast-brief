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


SVVSD_PAGE = "https://www.svvsd.org/about/board-of-education/board-meetings/2026-board-meetings/"
TPCA_FOLDER = "15YuyqNfAkNPcbaoT8r6SSedKGyZ6Box2"


def test_document_pages_and_drive_folders_are_checked_and_saved(tmp_path):
    page = S.make_source(
        "webpage",
        {"page_url": SVVSD_PAGE + "#top", "follow": " /boardmeetings/ ", "bodies": "Board"},
    )
    assert (page.url, page.follow) == (SVVSD_PAGE, "/boardmeetings/")
    bare = S.make_source("webpage", {"url": "www.svcmontessori.org/board-meetings", "bodies": "B"})
    assert (bare.url, bare.follow) == ("https://www.svcmontessori.org/board-meetings", "")
    by_link = S.make_source(
        "gdrive",
        {
            "folder_id": f"https://drive.google.com/drive/folders/{TPCA_FOLDER}?usp=sharing",
            "bodies": "TPCA Board",
        },
    )
    by_view = S.make_source(
        "gdrive",
        {
            "folder_id": f"https://drive.google.com/embeddedfolderview?id={TPCA_FOLDER}#list",
            "bodies": "TPCA Board",
        },
    )
    by_id = S.make_source("gdrive", {"folder_id": f" {TPCA_FOLDER} ", "bodies": "TPCA Board"})
    assert by_link == by_view == by_id
    assert by_id.id == f"gdrive:{TPCA_FOLDER}"
    assert page.id == f"webpage:{SVVSD_PAGE}"
    S.add_source(tmp_path, page)
    S.add_source(tmp_path, by_id)
    row = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))["sources"][0]
    assert (row["url"], row["follow"]) == (SVVSD_PAGE, "/boardmeetings/")
    assert S.load_sources(tmp_path) == [page, by_id]


@pytest.mark.parametrize(
    "kind,fields,field,match",
    [
        ("webpage", {"page_url": ""}, "page_url", "address of the page"),
        ("webpage", {"page_url": "ftp://x.org/a"}, "page_url", "https://"),
        ("webpage", {"page_url": "https://me:pw@x.org/"}, "page_url", "no user name"),
        ("webpage", {"page_url": "https://x.org/a b"}, "page_url", "spaces"),
        ("webpage", {"page_url": "https://x.org/", "follow": "boardmeetings"}, "follow", "path"),
        ("gdrive", {"folder_id": ""}, "folder_id", "folder's address"),
        ("gdrive", {"folder_id": "short"}, "folder_id", "not a Google Drive folder"),
        ("gdrive", {"folder_id": "https://drive.google.com/drive/folders/a b"}, "folder_id", "not"),
    ],
)
def test_bad_page_and_folder_fields_name_the_field(kind, fields, field, match):
    with pytest.raises(S.SourceError, match=match) as info:
        S.make_source(kind, {"bodies": "Board", **fields})
    assert info.value.field == field


def test_merging_adds_new_sources_and_only_the_missing_bodies(tmp_path):
    channel = S.make_source(
        "youtube", {"channel_id": "UCH5_wkpLrKYb1JuUk6-UdNg", "bodies": "City Council"}
    )
    S.add_source(tmp_path, channel)
    wider = S.make_source(
        "youtube",
        {"channel_id": "UCH5_wkpLrKYb1JuUk6-UdNg", "bodies": "city council, Water Board"},
    )
    folder = S.make_source("gdrive", {"folder_id": TPCA_FOLDER, "bodies": "TPCA Board"})
    _, changes = S.merge_sources(tmp_path, [wider, folder])
    assert changes == [
        "https://www.youtube.com/channel/UCH5_wkpLrKYb1JuUk6-UdNg now also covers Water Board",
        f"added {folder.address} for TPCA Board",
    ]
    saved = S.load_sources(tmp_path)
    assert saved[0].bodies == ("City Council", "Water Board")
    assert saved[1] == folder
    assert S.merge_sources(tmp_path, [wider, folder])[1] == []


@pytest.mark.parametrize(
    "source_id,ok",
    [
        (f"gdrive:{TPCA_FOLDER}", True),
        (f"webpage:{SVVSD_PAGE}", True),
        ("webpage:https://x.org/index.jsp?uREC_ID=560576&type=d", True),
        ("civicclerk:erieco", True),
        ("escribe:bouldercounty", True),
        ("civicplus:www.townoflyons.com", True),
        ("rss:https://x.org/", False),
        ("webpage:https://x.org/<script>", False),
    ],
)
def test_the_desk_knows_the_new_source_ids(source_id, ok):
    from civiccast_brief.review.web import _SOURCE_ID

    assert bool(_SOURCE_ID.match(source_id)) is ok


@pytest.mark.parametrize(
    "kind,fields,source_id,address",
    [
        (
            "civicclerk",
            {"clerk_tenant": "https://ErieCO.portal.civicclerk.com/#/home"},
            "civicclerk:erieco",
            "https://erieco.portal.civicclerk.com",
        ),
        ("civicclerk", {"tenant": " erieco "}, "civicclerk:erieco", None),
        (
            "escribe",
            {"escribe_tenant": "pub-bouldercounty.escribemeetings.com/?Year=2026"},
            "escribe:bouldercounty",
            "https://pub-bouldercounty.escribemeetings.com",
        ),
        (
            "civicplus",
            {"site": "https://www.townoflyons.com/AgendaCenter"},
            "civicplus:www.townoflyons.com",
            "https://www.townoflyons.com",
        ),
        ("civicplus", {"site": "www.lafayetteco.gov"}, "civicplus:www.lafayetteco.gov", None),
    ],
)
def test_meeting_platform_sources_take_an_address_or_a_tenant(
    tmp_path, kind, fields, source_id, address
):
    source = S.make_source(kind, {"bodies": "Town Council", **fields})
    assert source.id == source_id
    if address:
        assert source.address == address
    S.add_source(tmp_path, source)
    assert S.load_sources(tmp_path) == [source]


@pytest.mark.parametrize(
    "kind,fields,field,match",
    [
        ("civicclerk", {}, "clerk_tenant", "enter the tenant"),
        ("civicclerk", {"clerk_tenant": "https://erie.primegov.com"}, "clerk_tenant", "not that"),
        ("civicclerk", {"clerk_tenant": "erie co"}, "clerk_tenant", "not a tenant name"),
        ("escribe", {"escribe_tenant": "bouldercounty.escribe.com"}, "escribe_tenant", "not that"),
        ("civicplus", {"site": ""}, "site", "enter the town site"),
        ("civicplus", {"site": "https://lyons"}, "site", "not a site address"),
    ],
)
def test_bad_meeting_platform_fields_name_the_field(kind, fields, field, match):
    with pytest.raises(S.SourceError, match=match) as info:
        S.make_source(kind, {"bodies": "Board", **fields})
    assert info.value.field == field
