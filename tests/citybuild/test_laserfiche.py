"""Reading a body's Laserfiche WebLink folder, through a fake browser.

The listings are Louisville's, saved from Chrome on 2026-11-11: the Planning Commission folder
(8986), its 2026 folder (1058850) and its 2025 folder (1010632); and the viewer's answers for a
scanned Building Code Board of Appeals agenda (1066144): its page count and its first page's text.

Boulder's Planning Board listings (47549 and the folders under it) were saved the same day: Boulder
files a year by month and each month by meeting.
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


BOULDER = "https://documents.bouldercolorado.gov"


def boulder_file(docid: int) -> str:
    return f"{BOULDER}/WebLink/ElectronicFile.aspx?docid={docid}&dbid=0&repo=LF8PROD2"


def boulder_planning():
    url = f"{BOULDER}/WebLink/Browse.aspx?id=47549&dbid=0&repo=LF8PROD2"
    return make_source("laserfiche", {"url": url, "bodies": "Planning Board"})


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


def test_a_year_filed_by_month_and_meeting_is_read_down_to_the_meetings(fake, web):
    fake.files[boulder_file(206382)] = text_pdf("Planning Board", "Agenda", "October 27, 2026")
    proof = read_latest(
        boulder_planning(), "Planning Board", web.fetcher(), today=date(2026, 11, 11)
    )
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 27)
    assert proof.title == "10.27.26 PB Agenda"
    assert proof.document == boulder_file(206382)
    # The body, its two newest years, then the months newest first: November has no meetings yet.
    opened = [laserfiche.parse_url(u)[2] for u in fake.opened]
    assert opened[:6] == [47549, 198583, 189891, 198594, 198593, 206380]
    # Three months with meetings are read (October, September, August), not the whole year.
    assert 198591 in opened and 198590 not in opened
    assert fake.got == [boulder_file(206382)]


def test_a_meeting_folder_dated_after_today_is_not_opened(fake, web):
    fake.files[boulder_file(206375)] = text_pdf("Planning Board", "Agenda", "October 6, 2026")
    proof = read_latest(
        boulder_planning(), "Planning Board", web.fetcher(), today=date(2026, 10, 20)
    )
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 6)
    opened = [laserfiche.parse_url(u)[2] for u in fake.opened]
    assert 198594 not in opened  # November
    assert 206380 in opened and 206381 not in opened  # 10.06.26 is opened, 10.27.26 is not


def test_on_one_day_the_agenda_is_read_before_a_recording_listed_ahead_of_it(fake, web):
    meeting = fake.listings[206381]["data"]
    fake.listings[206381] = {"data": {**meeting, "results": meeting["results"][::-1]}}
    fake.files[boulder_file(206382)] = text_pdf("Planning Board", "Agenda", "October 27, 2026")
    proof = read_latest(
        boulder_planning(), "Planning Board", web.fetcher(), today=date(2026, 11, 11)
    )
    assert proof.ok, proof.problem
    assert fake.got == [boulder_file(206382)]


@pytest.mark.parametrize(
    "name,month",
    [("10 OCT", 10), ("09 SEPT", 9), ("2026", None), ("13 XYZ", None), ("10.06.26", None)],
)
def test_a_month_folder_is_known_by_its_name(name, month):
    assert laserfiche.month_of(name) == month


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


def boulder_body(folder: int, body: str):
    url = f"{BOULDER}/WebLink/Browse.aspx?id={folder}&dbid=0&repo=LF8PROD2"
    return make_source("laserfiche", {"url": url, "bodies": body})


def test_a_board_that_last_met_months_ago_is_read_past_its_empty_months(fake, web):
    """Boulder's Board of Zoning Adjustment (32770), saved on 2026-11-11: its 2026 folder has a
    folder for every month, and only January's has documents. Its own folder also holds a
    procedural rules document "Adopted 11.08.22", which is not a meeting."""
    fake.files[boulder_file(200960)] = text_pdf(
        "Board of Zoning Adjustment", "Disposition", "January 13, 2026"
    )
    proof = read_latest(
        boulder_body(32770, "Board of Zoning Adjustment"),
        "Board of Zoning Adjustment",
        web.fetcher(),
        today=date(2026, 11, 11),
    )
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 1, 13)
    assert proof.title == "01.13.26 BOZ2025-00013 Disposition Packet"
    assert fake.got == [boulder_file(200960)]


def test_an_address_that_is_a_boards_year_folder_is_read_as_that_year(fake, web):
    """Boulder's Open Space Board of Trustees address (198102) is the board's "2026" folder,
    which holds "01 JAN" ... "12 DEC" itself. October holds two meeting folders; the newest is
    the 30 October retreat."""
    fake.files[boulder_file(205847)] = text_pdf(
        "Open Space Board of Trustees", "Retreat", "October 30, 2026"
    )
    proof = read_latest(
        boulder_body(198102, "Open Space Board of Trustees"),
        "Open Space Board of Trustees",
        web.fetcher(),
        today=date(2026, 11, 11),
    )
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 10, 30)
    assert proof.document == boulder_file(205847)
    # The year folder itself, its empty November, then October and its two meetings.
    assert [laserfiche.parse_url(u)[2] for u in fake.opened][:3] == [198102, 198568, 198567]
