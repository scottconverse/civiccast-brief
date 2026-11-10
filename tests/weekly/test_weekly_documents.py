"""The weekly run's documents: a real adapter over the recorded Longmont portal answers.

The September 22 packet fixture holds packet pages 17-38, which carry the draft minutes of
the September 8 regular session. It is padded with 16 blank pages in front so its page
numbers are the packet's own.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from civiccast_brief.record.adapters.primegov import (
    ARCHIVED_MEETINGS_PATH,
    COMPILED_DOCUMENT_PATH,
    UPCOMING_MEETINGS_PATH,
    PrimeGovAdapter,
)
from civiccast_brief.weekly.documents import Fetcher, read_documents, write_documents

EVIDENCE = Path(__file__).resolve().parents[1] / "record" / "evidence" / "primegov"
PACKET_SEP22 = EVIDENCE / "longmont-packet-16823-minutes-sep08-p17-38.pdf"
BASE = "https://portal.test.invalid"
COUNCIL = "City Council"


def _padded_packet() -> bytes:
    writer = PdfWriter()
    first = PdfReader(PACKET_SEP22).pages[0]
    for _ in range(16):
        writer.add_blank_page(float(first.mediabox.width), float(first.mediabox.height))
    for page in PdfReader(PACKET_SEP22).pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def packet_sep22() -> bytes:
    return _padded_packet()


def _portal(packet: bytes, asked: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path == ARCHIVED_MEETINGS_PATH:
            if request.url.params.get("year") == "2026":
                return httpx.Response(
                    200, content=(EVIDENCE / "longmont-ListArchivedMeetings-2026.json").read_bytes()
                )
            return httpx.Response(200, json=[])
        if request.url.path == UPCOMING_MEETINGS_PATH:
            return httpx.Response(
                200, content=(EVIDENCE / "longmont-ListUpcomingMeetings.json").read_bytes()
            )
        if request.url.path == COMPILED_DOCUMENT_PATH:
            if request.url.params.get("meetingTemplateId") == "16823":
                return httpx.Response(
                    200, content=packet, headers={"content-type": "application/pdf"}
                )
            return httpx.Response(404)
        return httpx.Response(404)

    return handler


def _setup(packet: bytes, **limits):
    asked: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_portal(packet, asked)))
    adapter = PrimeGovAdapter(BASE, client, **limits)
    meetings = {m.id: m for m in adapter.list_meetings(date(2026, 9, 1), date(2026, 9, 30))}
    asked.clear()
    fetcher = Fetcher(adapter, pause_s=1.5, sleep=lambda s: asked.append(f"pause {s}"))
    return fetcher, meetings, asked


def test_the_sep_8_meeting_gets_its_draft_minutes_from_the_sep_22_packet(packet_sep22):
    fetcher, meetings, asked = _setup(packet_sep22)

    data, lines = read_documents(fetcher, meetings[3709], COUNCIL)

    # Its own packet (16807) is not on this portal: a line, not a failure.
    assert lines[0].startswith("packet: not read: ")
    assert "16807" in lines[0] and "404" in lines[0]
    assert lines[1] == "minutes: draft minutes on pages 17-38 of the September 22 packet"
    [minutes] = data["documents"]
    assert minutes["kind"] == "minutes" and minutes["draft"] is True
    assert (
        minutes["url"]
        == f"{BASE}{COMPILED_DOCUMENT_PATH}?meetingTemplateId=16823&compileOutputType=1"
    )
    assert [p["page"] for p in minutes["pages"]] == list(range(17, 39))
    assert minutes["pages"][0]["text"].startswith("MINUTES")
    assert "September 8, 2026" in minutes["pages"][0]["text"][:300]
    assert minutes["sha256"] == hashlib.sha256(packet_sep22).hexdigest()
    # A pause before every call to the portal after the first (a listing is two requests).
    assert [a.split("?")[0].rsplit("/", 1)[-1] for a in asked] == [
        "CompiledDocument",
        "pause 1.5",
        "ListArchivedMeetings",
        "ListUpcomingMeetings",
        "pause 1.5",
        "CompiledDocument",
    ]


def test_the_sep_22_packet_is_read_page_by_page_and_written(packet_sep22, tmp_path):
    fetcher, meetings, _ = _setup(packet_sep22)

    data, lines = read_documents(fetcher, meetings[3712], COUNCIL)

    assert lines[0] == "packet: 38 pages"
    assert lines[1].startswith("minutes: ")
    packet = data["documents"][0]
    assert packet["kind"] == "packet"
    assert [p["page"] for p in packet["pages"]] == list(range(1, 39))
    assert packet["pages"][0]["text"] == "" and packet["pages"][16]["text"].startswith("MINUTES")
    path = write_documents(tmp_path, data)
    assert path.name == "documents.json"
    assert json.loads(path.read_text(encoding="utf-8")) == data


def test_a_packet_over_the_adapter_limit_is_a_line_not_a_download(packet_sep22):
    fetcher, meetings, _ = _setup(packet_sep22, packet_limit_bytes=1000)

    data, lines = read_documents(fetcher, meetings[3712], COUNCIL)

    assert lines[0].startswith("packet: not read: ")
    assert "1000" in lines[0] or "limit" in lines[0].lower()
    assert all(d["kind"] != "packet" for d in data["documents"])


def test_a_meeting_the_portal_lists_no_packet_for_says_so(packet_sep22):
    fetcher, meetings, _ = _setup(packet_sep22)

    # The Joint Meeting with the County Commissioners lists an agenda only.
    data, lines = read_documents(fetcher, meetings[3799], COUNCIL)

    assert lines[0] == "packet: the portal lists no packet for this meeting"
    assert data["documents"] == [] or all(d["kind"] == "minutes" for d in data["documents"])
