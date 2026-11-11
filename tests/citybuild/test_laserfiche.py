"""Reading a body's Laserfiche WebLink folder, through a fake browser.

The listings are Louisville's, saved from Chrome on 2026-11-11: the Planning Commission folder
(8986), its 2026 folder (1058850) and its 2025 folder (1010632).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from civiccast_brief.citybuild import browser, laserfiche
from civiccast_brief.citybuild.fetch import FetchError, Page
from civiccast_brief.citybuild.read import read_latest
from civiccast_brief.sources import make_source

from .conftest import text_pdf

SAVED = Path(__file__).parent / "fixtures" / "laserfiche"
HOST = "https://laserfiche.louisvilleco.gov"
PLANNING = f"{HOST}/WebLink/Browse.aspx?id=8986&dbid=0&repo=Louisville"


def doc_url(docid: int) -> str:
    return f"{HOST}/WebLink/ElectronicFile.aspx?docid={docid}&dbid=0&repo=Louisville"


class FakeBrowser:
    """Folder pages answer their saved listing; documents answer the files added."""

    def __init__(self) -> None:
        self.listings: dict[Any, Any] = {
            int(p.stem.split("-")[1]): json.loads(p.read_text(encoding="utf-8"))
            for p in SAVED.glob("listing-*.json")
        }
        self.files: dict[str, bytes] = {}
        self.opened: list[str] = []
        self.posted: list[Any] = []
        self.got: list[str] = []

    def open_and_catch(self, url: str, part: str) -> Any:
        assert part == laserfiche.LISTING
        self.opened.append(url)
        folder = laserfiche.parse_url(url)[2]
        if folder not in self.listings:
            raise FetchError(f"{url}: the site stopped Chrome with a challenge page")
        return self.listings[folder]

    def post_json(self, url: str, data: Any, headers: dict[str, str] | None = None) -> Any:
        self.posted.append(data)
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
    """The 2026 folder's meetings are all after 5 January; "2000-2019" is not a year folder."""
    fake.files[doc_url(1058352)] = text_pdf("Planning Commission", "Agenda", "December 11, 2025")
    proof = read_latest(planning(), "Planning Commission", web.fetcher(), today=date(2026, 1, 5))
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2025, 12, 11)
    assert proof.document == doc_url(1058352)


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
