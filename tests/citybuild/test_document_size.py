"""A meeting document may be larger than a page: packets with plans and photos run past 8 MB."""

from __future__ import annotations

from datetime import date

import pytest

from civiccast_brief.citybuild import fetch
from civiccast_brief.citybuild.documents import read_document
from civiccast_brief.citybuild.fetch import FetchError

from .conftest import text_pdf

PACKET = "https://www.lafayetteco.gov/ArchiveCenter/ViewFile/Item/7281"


def big_pdf() -> bytes:
    """A real PDF with a large picture-like stream added after it, past the page limit."""
    pdf = text_pdf("Zoning Board of Adjustment", "Agenda", "October 9, 2024")
    padding = b"%" + b"x" * (fetch.MAX_BYTES + 1_000_000) + b"\n"
    head, tail = pdf.rsplit(b"startxref", 1)
    # The padding goes before the cross-reference offset's keyword, after every object, so the
    # file still opens: the offsets it lists are unchanged.
    return head + padding + b"startxref" + tail


def test_a_document_past_the_page_limit_is_read(web):
    web.add(PACKET, big_pdf(), "application/pdf")
    read = read_document(PACKET, web.fetcher(), "agenda")
    assert read.first_date == date(2024, 10, 9)
    assert read.lines[:2] == ["Zoning Board of Adjustment", "Agenda"]


def test_a_page_past_the_page_limit_is_still_refused(web):
    web.add(PACKET, b"<html>" + b"x" * (fetch.MAX_BYTES + 1) + b"</html>")
    with pytest.raises(FetchError, match=f"larger than {fetch.MAX_BYTES} bytes"):
        web.fetcher().get(PACKET)


def test_a_document_past_the_document_limit_is_refused(web, monkeypatch):
    monkeypatch.setattr(fetch, "DOCUMENT_BYTES", 1000)
    monkeypatch.setattr("civiccast_brief.citybuild.documents.DOCUMENT_BYTES", 1000)
    web.add(PACKET, big_pdf(), "application/pdf")
    with pytest.raises(FetchError, match="larger than 1000 bytes"):
        read_document(PACKET, web.fetcher(), "agenda")
