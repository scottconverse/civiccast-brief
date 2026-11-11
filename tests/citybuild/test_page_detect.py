"""Dates, links and platform addresses, against the shapes the Longmont and Boulder County pages use."""

from __future__ import annotations

from datetime import date

import pytest

from civiccast_brief.citybuild import detect, documents, drive
from civiccast_brief.citybuild.page import find_dates, link_date, parse

from .conftest import HERE, SVCM


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Board of Directors Meeting Agenda 2026-11-03 - DRAFT", date(2026, 11, 3)),
        ("September 10, 2026", date(2026, 9, 10)),
        ("DATE: 11/5/26", date(2026, 11, 5)),
        ("Wednesday, Dec. 16, 2026 at 6 p.m.", date(2026, 12, 16)),
        ("10 November 2026", date(2026, 11, 10)),
    ],
)
def test_dates_are_read_in_the_ways_the_pages_write_them(text, expected):
    assert find_dates(text)[0] == expected


def test_no_date_is_made_up():
    assert find_dates("Board Walkthrough Data") == []
    assert find_dates("13/45/2026 and February 30, 2026") == []


def test_links_carry_their_text_and_dates():
    text = (HERE / "svcm-board-meetings.html").read_text(encoding="utf-8")
    _, links, _ = parse(text, SVCM)
    docs = documents.document_links(links)
    assert len(docs) == 20
    newest = max(docs, key=lambda link: link_date(link) or date.min)
    assert link_date(newest) == date(2026, 9, 10)
    assert newest.url.startswith("https://docs.google.com/document/d/11PvZi1")


@pytest.mark.parametrize(
    "url,platform,key,extra",
    [
        ("https://bouldercolorado.primegov.com/public/portal", "primegov", "bouldercolorado", ""),
        ("https://erie.granicus.com/ViewPublisher.php?view_id=17", "granicus", "erie", "17"),
        ("https://erieco.portal.civicclerk.com/", "civicclerk", "erieco", ""),
        ("https://go.boarddocs.com/co/bvsd/Board.nsf/Public", "boarddocs", "co/bvsd", ""),
        ("https://boulder.legistar.com/Calendar.aspx", "legistar", "boulder", ""),
        (
            "https://www.youtube.com/channel/UCH5_wkpLrKYb1JuUk6-UdNg",
            "youtube",
            "UCH5_wkpLrKYb1JuUk6-UdNg",
            "",
        ),
        ("https://www.youtube.com/@cityoflongmont", "youtube", "", "@cityoflongmont"),
        ("https://www.youtube.com/cityoflongmont", "youtube", "", "cityoflongmont"),
        (
            "https://drive.google.com/drive/folders/15YuyqNfAkNPcbaoT8r6SSedKGyZ6Box2?usp=sharing",
            "gdrive",
            "15YuyqNfAkNPcbaoT8r6SSedKGyZ6Box2",
            "",
        ),
    ],
)
def test_platform_addresses_are_named(url, platform, key, extra):
    found = detect.classify(url)
    assert found is not None
    assert (found.platform, found.key, found.extra) == (platform, key, extra)


@pytest.mark.parametrize(
    "url",
    ["https://www.youtube.com/watch?v=abc", "https://longmontcolorado.gov/", "mailto:x@y.org"],
)
def test_ordinary_pages_are_not_platforms(url):
    assert detect.classify(url) is None


def test_documents_and_their_download_addresses():
    gdoc = "https://docs.google.com/document/d/1vHLNsTyco-HoUPkPIvZG_YtkbAAQkr8EnkFj8vHkCrU/edit"
    assert documents.is_document(gdoc)
    assert documents.download_url(gdoc).endswith("/export?format=txt")
    file = "https://drive.google.com/file/d/1qWbdBErR5yZtmQqR-I9Ue5ZUQgtYvehP/view"
    assert documents.download_url(file) == (
        "https://drive.google.com/uc?export=download&id=1qWbdBErR5yZtmQqR-I9Ue5ZUQgtYvehP"
    )
    assert documents.is_document("https://4.files.edl.io/x/26/a.PDF")
    assert not documents.is_document("https://www.svvsd.org/boardmeetings/meeting-180/")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Nov 6", date(2026, 11, 6)),
        ("Dec 2", date(2025, 12, 2)),
        ("3:14 PM", date(2026, 11, 10)),
        ("Nov 5, 2024", date(2024, 11, 5)),
        ("", None),
    ],
)
def test_drive_modified_dates(text, expected):
    assert drive.modified_date(text, date(2026, 11, 10)) == expected


def test_drive_entries_tell_folders_from_files():
    text = (HERE / "drive-tpca-november.html").read_text(encoding="utf-8")
    listed = drive.entries(text, date(2026, 11, 10))
    assert [(e.title, e.folder, e.modified) for e in listed] == [
        ("125746_ServiceTicket-2.pdf", False, date(2026, 11, 6)),
        ("November Agenda", False, date(2026, 11, 5)),
    ]
    root = drive.entries(
        (HERE / "drive-tpca-agendas.html").read_text(encoding="utf-8"), date(2026, 11, 10)
    )
    assert len(root) == 11 and all(e.folder for e in root)
