"""The weekly run's readers for sources that list documents and no video.

Each reader is run over the pages saved from the live sites (see conftest.py and
test_laserfiche.py): Erie's CivicClerk portal, Boulder County's eScribe calendar, Lyons' Agenda
Center, Lafayette's Archive Center, St. Vrain Valley's board meeting list, Twin Peaks' Drive
folder and Louisville's Planning Commission Laserfiche folder.
"""

from __future__ import annotations

import io
from datetime import date

from pypdf import PdfReader, PdfWriter

from civiccast_brief.citybuild import laserfiche
from civiccast_brief.sources import make_source
from civiccast_brief.weekly import listed

from .conftest import (
    BOCC_1105,
    BOULDER_COUNTY,
    ERIE,
    LAFAYETTE,
    LOVELAND,
    LYONS,
    SVVSD,
    SVVSD_180,
    SVVSD_PACKET,
    TPCA_AGENDA_DOC,
    TPCA_ROOT,
    add_boulder_county,
    add_erie,
    add_loveland,
    add_tpca_drive,
    text_pdf,
)
from .test_laserfiche import FakeBrowser, doc_url, fake, info, planning, text, view_url  # noqa: F401


def _days(docs):
    return sorted({(d.body, d.day) for d in docs})


def test_civicclerk_lists_each_bodys_documents_in_the_days(web):
    add_erie(web, date(2026, 11, 10))
    source = make_source("civicclerk", {"tenant": ERIE, "bodies": ["Town Council"]})
    docs = listed.list_docs(source, date(2026, 10, 21), date(2026, 11, 10), web.fetcher())
    # The 20 October special meeting is before the days, and Planning Commission is not listed.
    assert _days(docs) == [
        ("Town Council", date(2026, 10, 27)),
        ("Town Council", date(2026, 11, 10)),
    ]
    assert sorted(d.what for d in docs if d.day == date(2026, 11, 10)) == ["agenda", "packet"]


def test_escribe_keeps_only_the_listed_bodys_meetings(web):
    add_boulder_county(web)
    source = make_source(
        "escribe", {"tenant": BOULDER_COUNTY, "bodies": ["Board of County Commissioners"]}
    )
    docs = listed.list_docs(source, date(2026, 10, 20), date(2026, 11, 6), web.fetcher())
    # The Planning Commission (21 Oct) and Parks and Open Space (22 Oct) meetings on the county's
    # calendar are other bodies', and are not given to the one listed body.
    assert {d.title for d in docs} == {"Board of County Commissioners - Regular Meeting"}
    assert [d.day for d in docs].count(date(2026, 10, 22)) == 2  # its agenda and minutes
    assert {d.day for d in docs} == {date(2026, 10, 22), date(2026, 10, 27), date(2026, 11, 5)}
    assert any(BOCC_1105 in d.url and d.what == "agenda" for d in docs)


def test_civicplus_lists_the_bodys_agendas_from_either_center(web):
    web.fixture(f"https://{LYONS}/AgendaCenter", "civicplus-lyons-agendacenter.html")
    web.fixture(f"https://{LAFAYETTE}/Archive.aspx", "civicplus-lafayette-archive.html")
    first, last = date(2026, 10, 26), date(2026, 11, 10)
    lyons = make_source("civicplus", {"site": LYONS, "bodies": ["Board of Trustees"]})
    docs = listed.list_docs(lyons, first, last, web.fetcher())
    # The Planning & Community Development Commission agendas of 26 Oct and 9 Nov are not the
    # Board of Trustees'.
    assert not any("Planning" in d.title for d in docs)
    assert {d.day for d in docs} == {date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 10)}
    lafayette = make_source("civicplus", {"site": LAFAYETTE, "bodies": ["City Council"]})
    docs = listed.list_docs(lafayette, first, last, web.fetcher())
    assert [(d.day, d.url.rsplit("/", 1)[-1]) for d in docs] == [
        (date(2026, 11, 2), "8182"),
        (date(2026, 10, 27), "8168"),
    ]


def test_a_webpage_list_is_followed_to_its_meetings_documents(web):
    web.fixture(SVVSD, "svvsd-2026-board-meetings.html")
    web.fixture(SVVSD_180, "svvsd-meeting-180.html")
    source = make_source(
        "webpage", {"url": SVVSD, "follow": "/boardmeetings/", "bodies": ["Board of Education"]}
    )
    docs = listed.list_docs(source, date(2026, 11, 1), date(2026, 11, 12), web.fetcher())
    assert [(d.body, d.day, d.url) for d in docs] == [
        ("Board of Education", date(2026, 11, 11), SVVSD_PACKET)
    ]
    # "Member Events for November" (1 Nov) is on the list and is not a meeting.
    assert not any("member-events" in url for url in web.asked)


def test_a_drive_file_without_a_day_in_its_name_is_dated_from_its_text(web):
    add_tpca_drive(web)
    source = make_source("gdrive", {"folder_id": TPCA_ROOT, "bodies": ["Board of Directors"]})
    docs = listed.list_docs(source, date(2026, 11, 1), date(2026, 11, 30), web.fetcher())
    # "November Agenda" names no day; the agenda says 5 November.
    assert [(d.body, d.day, d.title) for d in docs] == [
        ("Board of Directors", date(2026, 11, 5), "November Agenda")
    ]
    assert TPCA_AGENDA_DOC in docs[0].url


def test_laserfiche_documents_are_marked_to_open_in_the_browser(fake, web):  # noqa: F811
    docs = listed.list_docs(planning(), date(2026, 10, 1), date(2026, 10, 31), web.fetcher())
    assert [(d.body, d.day, d.url, d.via) for d in docs] == [
        ("Planning Commission", date(2026, 10, 8), doc_url(1101058), "laserfiche")
    ]


# ----- Reading each document in full -----------------------------------------------------------


def _two_page_pdf() -> bytes:
    writer = PdfWriter()
    for page in ("Agenda", "Item 1. Call to order"), ("Item 2. Budget hearing",):
        writer.add_page(PdfReader(io.BytesIO(text_pdf(*page))).pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_read_all_keeps_every_page_and_reports_what_did_not_read(web):
    add_boulder_county(web)
    packet = "https://example.test/packet.pdf"
    web.add(packet, _two_page_pdf(), "application/pdf")
    escribe = (
        "https://pub-bouldercounty.escribemeetings.com/"
        f"Meeting.aspx?Id={BOCC_1105}&Agenda=Agenda&lang=English"
    )
    body = "Board of County Commissioners"
    docs = [
        listed.Doc(body, date(2026, 11, 5), "Regular Meeting", packet, "packet"),
        listed.Doc(body, date(2026, 11, 5), "Regular Meeting", escribe, "agenda"),
        listed.Doc(body, date(2026, 11, 5), "Regular Meeting", "https://example.test/gone.pdf", "minutes"),
    ]  # fmt: skip
    data, lines = listed.read_all(docs, web.fetcher())
    assert data["format"] == 1
    first, second = data["documents"]
    assert (first["kind"], first["url"]) == ("packet", packet)
    assert [p["page"] for p in first["pages"]] == [1, 2]
    assert "Budget hearing" in first["pages"][1]["text"]
    assert "Call to order" not in first["pages"][1]["text"]
    assert len(first["sha256"]) == 64
    # The eScribe agenda is a web page; its numbered items are its one page.
    assert second["kind"] == "agenda" and len(second["pages"]) == 1
    assert "1. Call to Order" in second["pages"][0]["text"]
    assert lines[0] == f"packet: 2 pages ({packet})"
    assert lines[2].startswith("minutes: not read: ")


def test_a_scanned_laserfiche_document_is_read_past_three_pages_one_page_each(fake, web):  # noqa: F811
    """The city check reads three pages of a scan; the weekly run reads all of them."""
    fake.infos[1066144] = info(5)
    for n in range(1, 6):
        fake.texts[(1066144, n)] = text(f"Page {n} of the agenda")
    doc = listed.Doc("Planning Commission", date(2026, 2, 11), "Agenda", view_url(1066144), "agenda", "laserfiche")  # fmt: skip
    _sha, pages, _unread = listed.read_pages(doc, web.fetcher())
    assert pages == [f"Page {n} of the agenda" for n in range(1, 6)]
    # The city check's reader still stops at three.
    page = laserfiche.Reader(fake).get(view_url(1066144))
    assert page.content.decode().split("\f") == [f"Page {n} of the agenda" for n in range(1, 4)]


def test_a_laserfiche_folder_that_times_out_once_is_tried_again(monkeypatch, web):
    from civiccast_brief.citybuild.fetch import FetchError
    from civiccast_brief.citybuild.platforms import Doc as Listed

    calls = []

    def docs(browser, url, body, today):
        calls.append(url)
        if len(calls) == 1:
            raise FetchError(f"{url} did not open in Chrome: Timeout 60000ms exceeded")
        return [Listed(body, date(2026, 10, 8), "Agenda", doc_url(1101058), "agenda")], []

    monkeypatch.setattr(laserfiche, "docs", docs)
    monkeypatch.setattr(listed.browser, "shared", lambda: None)
    found = listed.list_docs(planning(), date(2026, 10, 1), date(2026, 10, 31), web.fetcher())
    assert len(calls) == 2
    assert [d.url for d in found] == [doc_url(1101058)]


def test_a_document_larger_than_the_city_checks_cap_is_read_whole(monkeypatch, web):
    from civiccast_brief.citybuild import fetch

    # The city check stops at DOCUMENT_BYTES; the weekly run's cap is far above it.
    assert listed.WHOLE_BYTES > fetch.DOCUMENT_BYTES * 5
    pdf = _two_page_pdf()
    monkeypatch.setattr(listed, "WHOLE_BYTES", len(pdf) + 1)
    url = "https://example.test/packet.pdf"
    web.add(url, pdf, "application/pdf")
    doc = listed.Doc("City Council", date(2026, 11, 2), "Packet", url, "packet")
    fetcher = web.fetcher()
    fetcher.max_bytes = len(pdf) - 1  # a default cap the packet is over
    _sha, pages, _unread = listed.read_pages(doc, fetcher)
    assert len(pages) == 2


def test_a_pdf_page_pypdf_will_not_parse_is_reported_and_the_rest_read(monkeypatch, web):
    """One page of Louisville's 2 November 2026 council packet is past pypdf's token limit."""
    from pypdf import PageObject
    from pypdf.errors import LimitReachedError

    real = PageObject.extract_text

    def extract(page, *args, **kwargs):
        text = real(page, *args, **kwargs)
        if "Budget hearing" in text:
            raise LimitReachedError("Reached maximum number of tokens per stream.")
        return text

    monkeypatch.setattr(PageObject, "extract_text", extract)
    url = "https://example.test/packet.pdf"
    web.add(url, _two_page_pdf(), "application/pdf")
    doc = listed.Doc("City Council", date(2026, 11, 2), "Packet", url, "packet")
    data, lines = listed.read_all([doc], web.fetcher())
    pages = data["documents"][0]["pages"]
    assert "Call to order" in pages[0]["text"] and pages[1]["text"] == ""
    assert lines == [f"packet: 2 pages ({url}); no text taken from page 2"]


def test_a_listed_file_that_is_not_a_document_is_reported_not_read_as_pages(web):
    """Boulder's Laserfiche folders list the meeting's MP3 recording beside its agenda (Planning
    Board, 27 October 2026, 94 MB). Its bytes are not split into pages at each form feed."""
    url = "https://example.test/recording"
    sound = b"ID3\x03\x00\x00\x00\x006\x00TY" + b"\xff\xfb\x90\x0c" * 200
    web.add(url, sound, "application/octet-stream")
    doc = listed.Doc("Planning Board", date(2026, 10, 27), "Agenda", url, "agenda")
    data, lines = listed.read_all([doc], web.fetcher())
    assert data["documents"] == []
    assert lines[0].startswith("agenda: not read: ")
    assert "application/octet-stream" in lines[0]


def test_civicweb_lists_the_listed_bodys_documents_in_the_days(web):
    add_loveland(web, date(2026, 11, 12))
    source = make_source("civicweb", {"tenant": LOVELAND, "bodies": ["City Council"]})
    docs = listed.list_docs(source, date(2026, 11, 1), date(2026, 11, 12), web.fetcher())
    # The 9 November Planning Commission meeting is not the listed body's, and is not asked for;
    # the 17 and 19 November council meetings are after the run's last day.
    assert _days(docs) == [
        ("City Council", date(2026, 11, 4)),
        ("City Council", date(2026, 11, 10)),
    ]
    assert [(d.day, d.what) for d in docs] == [
        (date(2026, 11, 10), "agenda"),
        (date(2026, 11, 10), "packet"),
        (date(2026, 11, 4), "agenda"),
        (date(2026, 11, 4), "packet"),
    ]
    portal = "https://cilovelandco.civicweb.net"
    assert docs[1].url == (
        f"{portal}/document/522975/"
        "LOVELAND%20CITY%20COUNCIL%20STUDY%20SESSION%20-%2010%20Nov%202026.pdf"
    )
    assert not any("18182" in url for url in web.asked)


def test_civicweb_lists_the_adopted_minutes_of_a_meeting(web):
    last = date(2026, 8, 25)
    add_loveland(web, last)
    source = make_source("civicweb", {"tenant": LOVELAND, "bodies": ["City Council"]})
    docs = listed.list_docs(source, date(2026, 8, 19), last, web.fetcher())
    assert [(d.day, d.what) for d in docs] == [
        (date(2026, 8, 19), "agenda"),
        (date(2026, 8, 19), "packet"),
        (date(2026, 8, 19), "minutes"),
    ]
    assert docs[2].url.startswith("https://cilovelandco.civicweb.net/document/520275/")
    assert docs[2].url.endswith(".pdf")


def test_a_civicweb_agenda_page_is_read_as_its_lines_of_text(web):
    import json

    import pytest

    from civiccast_brief.citybuild.fetch import FetchError

    from .conftest import HERE

    rows = json.loads((HERE / "civicweb-loveland-docs-18182.json").read_text("utf-8"))
    (agenda,) = [row for row in rows if row["DocumentType"] == 1]
    url = f"https://cilovelandco.civicweb.net/document/{agenda['Id']}/Agenda.html"
    web.add(url, agenda["Html"])
    doc = listed.Doc("Planning Commission", date(2026, 11, 9), "Agenda", url, "agenda")
    sha, pages, unread = listed.read_pages(doc, web.fetcher())
    assert len(sha) == 64 and unread == []
    assert len(pages) == 1
    assert "I. CALL TO ORDER\nII. PLEDGE OF ALLEGIANCE\nIII. ROLL CALL" in pages[0]
    # A web page that is not a CivicWeb document is still not taken for one.
    other_url = "https://cilovelandco.civicweb.net/other"
    web.add(other_url, "<html><body>Sign in</body></html>")
    other = listed.Doc("Planning Commission", date(2026, 11, 9), "x", other_url, "agenda")
    with pytest.raises(FetchError, match="web page, not the document"):
        listed.read_pages(other, web.fetcher())
