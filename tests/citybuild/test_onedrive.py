"""Reading minutes a town links as OneDrive shares, through a fake browser.

Jamestown, Colorado's Board of Trustees minutes page, saved on 2026-11-11, links each meeting's
minutes as a ``1drv.ms`` share named for its day ("05 11 2026 Minutes", "2025 12 15 BOT
Minutes"). The files were downloaded from Chrome the same day: the 11 May 2026 minutes, a Word
share; the 13 April 2026 minutes, a PDF share; and the PDF viewer's answer for that item.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from civiccast_brief.citybuild import browser
from civiccast_brief.citybuild.fetch import FetchError, Page
from civiccast_brief.citybuild.read import read_latest
from civiccast_brief.sources import make_source

SAVED = Path(__file__).parent / "fixtures" / "onedrive"
MINUTES = "https://jamestownco.org/board-of-trustees-meeting-agendas/minutes/"
MAY = (
    "https://1drv.ms/w/c/88066315599a8236/IQCQh87Rn6CXQrdlUShL3ZCsAaVyAjZrFWfgiAF_L1xZNSA?e=qg0BNo"
)
APRIL = (
    "https://1drv.ms/b/c/88066315599a8236/IQCip_3FtPMNRqIwPhGH-J7HAVggHKzXKAI25SpcZgn-ngI?e=00DErp"
)
APRIL_FILE = (
    "https://onedrive.live.com/personal/88066315599a8236/_layouts/15/download.aspx"
    "?UniqueId=c5fda7a2-f3b4-460d-a230-3e1187f89ec7&Translate=false&ApiVersion=2.0"
)
TODAY = date(2026, 11, 11)


class FakeBrowser:
    """Downloads answer the files added; the PDF viewer answers its saved item."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.items: dict[str, Any] = {}
        self.opened: list[tuple[str, str]] = []
        self.got: list[str] = []

    def open_and_catch(self, url: str, part: str) -> Any:
        self.opened.append((url, part))
        if url not in self.items:
            raise FetchError(f"{url} did not open in Chrome")
        return self.items[url]

    def post_json(self, url: str, data: Any, headers: dict[str, str] | None = None) -> Any:
        raise AssertionError("nothing is posted for a OneDrive share")

    def get(self, url: str) -> Page:
        self.got.append(url)
        if url not in self.files:
            raise FetchError(f"{url} did not download in Chrome")
        content = self.files[url]
        return Page(url, 200, content, browser.kind_of_file(content))


@pytest.fixture
def fake():
    reader = FakeBrowser()
    browser.use(reader)
    yield reader
    browser.use(None)


@pytest.fixture
def jamestown(web):
    web.add(MINUTES, (SAVED / "jamestown-minutes-2026-11-11.html").read_bytes(), "text/html")
    return make_source("webpage", {"url": MINUTES, "bodies": "Board of Trustees"})


def test_the_newest_minutes_a_town_shares_from_onedrive_are_read_in_the_browser(
    fake, web, jamestown
):
    """The newest is a Word share: it downloads with ``download=1`` and its paragraphs are read."""
    fake.files[MAY + "&download=1"] = (SAVED / "minutes-2026-05-11.docx").read_bytes()
    proof = read_latest(jamestown, "Board of Trustees", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 5, 11)
    assert proof.title == "05 11 2026 Minutes"
    assert proof.document == MAY
    assert proof.what == "minutes"
    assert proof.lines == [
        "first date in it: 2026-05-11",
        "Town of Jamestown",
        "May 11, 2026 Meeting Minutes",
        (
            "Mayor Box called the meeting to order at 7:04 p.m. Present at roll call were Trustees "
            "Annamaria Boehms, Roger Loving, Steven Oncley and Louis Maurer. Trustees M"
        ),
        (
            "Approval of Agenda: Trustee Oncley moved to approve the agenda seconded by Trustee "
            "Boehms. The motion passed with four yes votes."
        ),
    ]
    # The page is read with the plain client; the share only in the browser.
    assert web.asked == [MINUTES]
    assert fake.got == [MAY + "&download=1"]


def test_a_pdf_share_downloads_from_the_address_its_viewer_gives_on_onedrive_live_com(
    fake, web, jamestown
):
    """When the May minutes do not download, the April minutes before them are read: a PDF
    share, whose viewer gives a my.microsoftpersonalcontent.com address that refuses it."""
    fake.items[APRIL] = json.loads((SAVED / "item-2026-04-13.json").read_text(encoding="utf-8"))
    fake.files[APRIL_FILE] = (SAVED / "minutes-2026-04-13.pdf").read_bytes()
    proof = read_latest(jamestown, "Board of Trustees", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.meeting_date == date(2026, 4, 13)
    assert proof.document == APRIL
    assert proof.lines[1:4] == ["Town Board Meeting", "April 13, 2026", "Meeting Minutes"]
    assert fake.opened == [(APRIL, "content.downloadUrl")]
    assert fake.got == [MAY + "&download=1", APRIL_FILE]
