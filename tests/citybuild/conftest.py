"""Shared pieces for the city search tests.

No test reaches the network. :class:`Web` answers each address from a saved page (the fixtures
here were trimmed from the live pages on 2026-11-10) or from bytes a test gives, and anything else
answers 404, the way a missing page would.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from civiccast_brief.citybuild.fetch import Fetcher
from civiccast_brief.record import pacing

HERE = Path(__file__).parent / "fixtures"
PRIMEGOV_FIXTURES = Path(__file__).parent.parent / "record" / "adapters" / "fixtures"

LONGMONT_HOME = "https://longmontcolorado.gov/"
PRIMEGOV = "https://longmont.primegov.com"
SVVSD = "https://www.svvsd.org/about/board-of-education/board-meetings/2026-board-meetings/"
SVVSD_180 = "https://www.svvsd.org/boardmeetings/board-of-education-regular-meeting-180/"
SVVSD_PACKET = "https://www.svvsd.org/wp-content/uploads/2026/01/4.0-11.11.26-FINAL-Packet.pdf"
FLAGSTAFF = (
    "https://www.flagstaffacademy.org/apps/pages/index.jsp?uREC_ID=560576&type=d&pREC_ID=2760940"
)
SVCM = "https://www.svcmontessori.org/board-meetings"
SVCM_NEWEST = "11PvZi1oodu1OZznCoESf9V6w_qTn4Hrh5gMsk854sLs"
TPCA = "https://www.twinpeaksclassical.org/page/governance"
TPCA_ROOT = "15YuyqNfAkNPcbaoT8r6SSedKGyZ6Box2"
TPCA_AGENDAS = "1L3vNUaqhFmoESFiDpTIKV9VdEwcAWNTL"
TPCA_NOVEMBER = "1aXwFhu3a8ZKZ63v7itaiANcz-ujS8QjV"
TPCA_AGENDA_DOC = "1vHLNsTyco-HoUPkPIvZG_YtkbAAQkr8EnkFj8vHkCrU"
CHANNEL = "UCH5_wkpLrKYb1JuUk6-UdNg"


def gdoc_export(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/export?format=txt"


def folder_view(folder_id: str) -> str:
    return f"https://drive.google.com/embeddedfolderview?id={folder_id}"


def feed(*videos: tuple[str, str, str]) -> bytes:
    """A YouTube channel feed of (video id, title, published) entries."""
    entries = "".join(
        f"<entry><id>yt:video:{v}</id><yt:videoId>{v}</yt:videoId><title>{t}</title>"
        f"<published>{p}</published></entry>"
        for v, t, p in videos
    )
    return f'<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015">{entries}</feed>'.encode()


def text_pdf(*lines: str) -> bytes:
    """A small one-page PDF whose text pypdf reads back."""
    stream = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({line}) Tj 0 -16 Td" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out


class Web:
    """Saved answers by address; every request is kept in ``asked``."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, bytes, str]] = {}
        self.asked: list[str] = []

    def add(
        self,
        url: str,
        body: bytes | str,
        content_type: str = "text/html; charset=utf-8",
        status: int = 200,
    ) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.pages[url] = (status, data, content_type)

    def fixture(self, url: str, name: str, content_type: str = "text/html; charset=utf-8") -> None:
        folder = PRIMEGOV_FIXTURES if (PRIMEGOV_FIXTURES / name).exists() else HERE
        self.add(url, (folder / name).read_bytes(), content_type)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.asked.append(url)
        if url not in self.pages:
            return httpx.Response(404, content=b"not found")
        status, body, content_type = self.pages[url]
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    def fetcher(self) -> Fetcher:
        client = httpx.Client(transport=httpx.MockTransport(self.handle), follow_redirects=True)
        return Fetcher(client=client)


def add_primegov(web: Web, base: str = PRIMEGOV) -> None:
    """The Longmont portal: its committees, the 2026 archive, the upcoming list, two agendas."""
    web.fixture(
        base + "/api/committee/GetCommitteeesListByShowInPublicPortal",
        "primegov-committees-longmont.json",
        "application/json",
    )
    web.fixture(
        base + "/api/v2/PublicPortal/ListArchivedMeetings?year=2026",
        "archived-meetings-2026.json",
        "application/json",
    )
    web.add(base + "/api/v2/PublicPortal/ListArchivedMeetings?year=2025", "[]", "application/json")
    web.fixture(
        base + "/api/v2/PublicPortal/ListUpcomingMeetings",
        "upcoming-meetings.json",
        "application/json",
    )
    web.fixture(base + "/Portal/Meeting?meetingTemplateId=16826", "agenda-16805.html")
    web.fixture(base + "/Portal/Meeting?meetingTemplateId=16821", "agenda-16805.html")
    web.fixture(base + "/Portal/Meeting?meetingTemplateId=16535", "agenda-16095.html")


def add_tpca_drive(web: Web) -> None:
    web.fixture(folder_view(TPCA_ROOT), "drive-tpca-root.html")
    web.fixture(folder_view(TPCA_AGENDAS), "drive-tpca-agendas.html")
    web.fixture(folder_view(TPCA_NOVEMBER), "drive-tpca-november.html")
    web.fixture(gdoc_export(TPCA_AGENDA_DOC), "gdoc-tpca-november-agenda.txt", "text/plain")


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def pace(tmp_path: Path) -> Iterator[pacing.Pacer]:
    """A process pace on a clock the sleeps move, so a YouTube wait never sleeps for real."""
    clock = _Clock()
    installed = pacing.Pacer(clock=clock, sleep=clock.sleep)
    pacing.use_pacer(installed)
    try:
        yield installed
    finally:
        pacing.use_pacer(None)


@pytest.fixture
def web() -> Web:
    return Web()


def data_api(*videos: tuple[str, str, str]):
    """A fake YouTube Data API answering the three calls with (video id, title, published)."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/channels"):
            uploads = {"relatedPlaylists": {"uploads": "UU" + CHANNEL[2:]}}
            return httpx.Response(200, json={"items": [{"contentDetails": uploads}]})
        if path.endswith("/playlistItems"):
            items = [
                {
                    "snippet": {"title": t, "publishedAt": p, "channelId": CHANNEL},
                    "contentDetails": {"videoId": v, "videoPublishedAt": p},
                }
                for v, t, p in videos
            ]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(200, json={"items": []})

    return handle
