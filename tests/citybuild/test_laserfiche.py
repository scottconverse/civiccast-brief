"""Reading a body's Laserfiche WebLink folder, through a fake browser.

The listings are Louisville's, saved from Chrome on 2026-11-11: the Planning Commission folder
(8986), its 2026 folder (1058850) and its 2025 folder (1010632); and the viewer's answers for a
scanned Building Code Board of Appeals agenda (1066144): its page count and its first page's text.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from civiccast_brief.citybuild import browser, laserfiche
from civiccast_brief.citybuild.documents import read_document
from civiccast_brief.citybuild.fetch import FetchError, Page
from civiccast_brief.citybuild.read import read_latest
from civiccast_brief.sources import make_source

from .conftest import text_pdf

SAVED = Path(__file__).parent / "fixtures" / "laserfiche"
HOST = "https://laserfiche.louisvilleco.gov"
PLANNING = f"{HOST}/WebLink/Browse.aspx?id=8986&dbid=0&repo=Louisville"


def doc_url(docid: int) -> str:
    return f"{HOST}/WebLink/ElectronicFile.aspx?docid={docid}&dbid=0&repo=Louisville"


def view_url(docid: int) -> str:
    return f"{HOST}/WebLink/DocView.aspx?id={docid}&dbid=0&repo=Louisville"


def saved(name: str) -> Any:
    return json.loads((SAVED / name).read_text(encoding="utf-8"))


def info(pages: int) -> Any:
    return {"data": {"pageCount": pages}}


def text(page: str) -> Any:
    return {"data": {"text": page}}


class FakeBrowser:
    """Folder pages answer their saved listing; files and viewer pages answer what is added."""

    def __init__(self) -> None:
        self.listings: dict[Any, Any] = {
            int(p.stem.split("-")[1]): json.loads(p.read_text(encoding="utf-8"))
            for p in SAVED.glob("listing-*.json")
        }
        self.files: dict[str, bytes] = {}
        self.infos: dict[int, Any] = {}
        self.texts: dict[tuple[int, int], Any] = {}
        self.opened: list[str] = []
        self.posted: list[Any] = []
        self.got: list[str] = []

    def open_and_catch(self, url: str, part: str) -> Any:
        self.opened.append(url)
        if part == laserfiche.INFO:
            docid = int(url.split("id=")[1].split("&")[0])
            if docid not in self.infos:
                raise FetchError(f"{url} answered 404 in Chrome")
            return self.infos[docid]
        assert part == laserfiche.LISTING
        folder = laserfiche.parse_url(url)[2]
        if folder not in self.listings:
            raise FetchError(f"{url}: the site stopped Chrome with a challenge page")
        return self.listings[folder]

    def post_json(self, url: str, data: Any, headers: dict[str, str] | None = None) -> Any:
        self.posted.append(data)
        if url.endswith(laserfiche.TEXT):
            assert url == "/WebLink" + laserfiche.TEXT
            return self.texts[(data["documentId"], data["pageNum"])]
        assert url == "/WebLink" + laserfiche.LISTING
        return self.listings[("all", data["folderId"])]

    def get(self, url: str) -> Page:
        self.got.append(url)
        if url not in self.files:
            raise FetchError(f"{url} answered 404 in Chrome")
        content = self.files[url]
        return Page(url, 200, content, browser.kind_of_file(content))


@pytest.fixture
def fake():
    reader = FakeBrowser()
    browser.use(reader)
    yield reader
    browser.use(None)


def planning():
    return make_source("laserfiche", {"url": PLANNING, "bodies": "Planning Commission"})


def test_the_newest_past_agenda_in_the_body_folder_is_read_in_the_browser(fake, web):
    fake.files[doc_url(1101058)] = text_pdf(
        "Planning Commission", "Agenda", "Thursday, October 8, 2026"
    )
    proof = read_latest(planning(), "Planning Commission", web.fetcher(), today=date(2026, 11, 11))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 8)
    assert proof.title == "Planning Commission Agenda and Packet 2026 10 08"
    assert proof.document == doc_url(1101058)
    assert proof.lines[0] == "1 pages, first date in it: 2026-10-08"
    assert proof.lines[1:3] == ["Planning Commission", "Agenda"]
    # The body folder, then its two newest year folders; the plain client asks nothing.
    assert [laserfiche.parse_url(u)[2] for u in fake.opened] == [8986, 1058850, 1010632]
    assert fake.got == [doc_url(1101058)]
    assert web.asked == []


def test_in_january_last_years_folder_has_the_newest_meeting(fake, web):
    """The 2026 folder's meetings are all after 5 January; "2000-2019" is not a year folder.

    The 2025 documents are scans, so the newest is read from its viewer's page text.
    """
    fake.infos[1058352] = info(2)
    fake.texts[(1058352, 1)] = text("Planning Commission\r\n<b>Agenda</b>\r\nDecember 11, 2025")
    fake.texts[(1058352, 2)] = text("Call to order")
    proof = read_latest(planning(), "Planning Commission", web.fetcher(), today=date(2026, 1, 5))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2025, 12, 11)
    assert proof.document == view_url(1058352)
    assert proof.lines == [
        "first date in it: 2025-12-11",
        "Planning Commission",
        "Agenda",
        "December 11, 2025",
        "Call to order",
    ]
    assert fake.got == []


def test_a_scanned_document_is_read_from_the_text_its_viewer_shows():
    """Louisville's Building Code Board of Appeals agenda for 11 February 2026 is a 31-page scan.

    ElectronicFile.aspx answers it with WebLink's error page; its viewer shows the text.
    """
    fake = FakeBrowser()
    fake.infos[1066144] = saved("info-1066144.json")
    fake.texts[(1066144, 1)] = saved("text-1066144-1.json")
    fake.texts[(1066144, 2)] = text("")
    fake.texts[(1066144, 3)] = text("")
    read = read_document(view_url(1066144), laserfiche.Reader(fake), "agenda")
    assert read.first_date == date(2026, 2, 11)
    assert read.what == "agenda"
    # The scan's own text, tags and spacing taken out: the city seal's letters come first.
    assert read.lines == ["IU Cityo", "f", "Louisville", "COLORADO-SINCE 1878"]
    # Three pages are asked for, as for a PDF; the 31-page scan is not read whole.
    assert [d["pageNum"] for d in fake.posted] == [1, 2, 3]
    assert fake.got == []


def test_a_scan_with_no_text_is_reported_not_read():
    fake = FakeBrowser()
    fake.infos[1066144] = info(1)
    fake.texts[(1066144, 1)] = text("  \r\n <br/> ")
    with pytest.raises(FetchError, match="no text to read"):
        read_document(view_url(1066144), laserfiche.Reader(fake), "agenda")


def test_a_document_that_does_not_open_gives_way_to_the_next_newest(fake, web):
    fake.files[doc_url(1089985)] = text_pdf("Planning Commission", "Agenda", "July 30, 2026")
    proof = read_latest(planning(), "Planning Commission", web.fetcher(), today=date(2026, 11, 11))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 7, 30)
    assert fake.got == [doc_url(1101058), doc_url(1089985)]


def test_a_folder_longer_than_the_pages_list_is_asked_for_all_of_it(fake, web):
    short = fake.listings[1058850]
    rows = short["data"]["results"]
    late = {**rows[-1], "entryId": 2000001, "name": "Planning Commission Agenda 2026 11 05"}
    fake.listings[1058850] = {"data": {**short["data"], "totalEntries": len(rows) + 1}}
    fake.listings[("all", 1058850)] = {"data": {**short["data"], "results": [*rows, late]}}
    fake.files[doc_url(2000001)] = text_pdf("Planning Commission", "Agenda", "November 5, 2026")
    proof = read_latest(planning(), "Planning Commission", web.fetcher(), today=date(2026, 11, 11))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 11, 5)
    assert fake.posted == [
        {
            "repoName": "Louisville",
            "folderId": 1058850,
            "getNewListing": True,
            "start": 0,
            "end": len(rows) + 1,
            "sortColumn": "",
            "sortAscending": True,
        }
    ]


def test_a_site_that_stops_the_browser_is_reported_not_read(fake, web):
    fake.listings.pop(8986)
    proof = read_latest(planning(), "Planning Commission", web.fetcher(), today=date(2026, 11, 11))
    assert not proof.ok
    assert "stopped Chrome with a challenge page" in proof.problem
    assert fake.got == []


@pytest.mark.parametrize(
    "name,year",
    [
        ("2026 Planning Commission Agendas Packets Minutes", 2026),
        ("2000-2019 Planning Commission", None),
        ("1999 & PRIOR Horticulture and Forestry Advisory Board Agendas and Pack", None),
        ("Planning Commission BYLAWS", None),
    ],
)
def test_only_a_single_years_folder_is_a_year_folder(name, year):
    assert laserfiche.year_of(name) == year


def test_a_challenge_page_is_known_and_a_pdf_is_known_by_its_bytes():
    page = "<!DOCTYPE html><html><head><title>Just a moment...</title>"
    assert browser.challenged(403, page)
    assert not browser.challenged(200, page)
    assert not browser.challenged(403, "<html>Forbidden</html>")
    assert browser.kind_of_file(b"%PDF-1.7") == "application/pdf"
    assert browser.kind_of_file(b"  <!doctype html>") == "text/html"
