"""List eScribe meetings that have a Video link and resolve their iSiLive media.

Boulder County is the first configured tenant. The calendar endpoint returns
ASP.NET's ``{"d": [...]}`` wrapper; each Video document points at the public
standalone player. The player supplies the iSiLive client and file name, from
which both the MP4 and its colocated VTT caption file are built.
"""

from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx

from civiccast_brief.record.video.youtube.listing import (
    SOURCE_ESCRIBE,
    VideoError,
    VideoListing,
)

ESCRIBE_CALENDAR = "/MeetingsCalendarView.aspx/GetCalendarMeetings"
ESCRIBE_BASE = "https://pub-{tenant}.escribemeetings.com"
ISILIVE_BASE = "https://video.isilive.ca"
DEFAULT_TENANT = "bouldercounty"


def escribe_base(tenant: str) -> str:
    return ESCRIBE_BASE.format(tenant=tenant.strip())


def isilive_media_url(client_id: str, file_name: str) -> str:
    """The public iSiLive MP4 address."""
    client = quote(client_id.strip(), safe="")
    name = quote(file_name.strip(), safe="")
    return f"{ISILIVE_BASE}/{client}/{name}"


def _meeting_day(row: dict[str, Any]) -> date | None:
    text = str(row.get("StartDate") or "").strip()
    try:
        return datetime.strptime(text[:19], "%Y/%m/%d %H:%M:%S").date()
    except ValueError:
        return None


def _duration(row: dict[str, Any]) -> int | None:
    start = str(row.get("StartDate") or "").strip()
    end = str(row.get("EndDate") or "").strip()
    try:
        start_at = datetime.strptime(start[:19], "%Y/%m/%d %H:%M:%S")
        end_at = datetime.strptime(end[:19], "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None
    seconds = int((end_at - start_at).total_seconds())
    return seconds if seconds >= 0 else None


def video_link(row: dict[str, Any]) -> dict[str, Any] | None:
    """The meeting's first document that is explicitly a Video link."""
    for link in row.get("MeetingDocumentLink") or []:
        if not isinstance(link, dict):
            continue
        words = {
            str(link.get("Type") or "").casefold(),
            str(link.get("Format") or "").casefold(),
            str(link.get("Title") or "").casefold(),
        }
        if (link.get("HasVideo") is True or "video" in words) and str(
            link.get("Url") or ""
        ).strip():
            return link
    return None


class _PlayerAttributes(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.client_id = ""
        self.file_name = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "div":
            return
        values = {name.casefold(): value or "" for name, value in attrs}
        if values.get("id") != "isi_player":
            return
        self.client_id = values.get("data-client_id", "").strip()
        self.file_name = values.get("data-file_name", "").strip()


def parse_player(html: str | bytes) -> tuple[str, str]:
    """Read ``data-client_id`` and ``data-file_name`` from a standalone player."""
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    parser = _PlayerAttributes()
    try:
        parser.feed(text)
    except ValueError as exc:
        raise VideoError(f"the eScribe player page could not be parsed: {exc}") from exc
    if not parser.client_id or not parser.file_name:
        raise VideoError("the eScribe player page held no iSiLive client and file name")
    return parser.client_id, parser.file_name


class EScribeLister:
    """Reads an eScribe calendar over an :class:`httpx.Client` the caller owns."""

    def __init__(
        self,
        client: httpx.Client,
        tenant: str = DEFAULT_TENANT,
        *,
        base_url: str | None = None,
    ) -> None:
        self.client = client
        self.tenant = tenant.strip()
        self.base_url = (base_url or escribe_base(self.tenant)).rstrip("/")

    @property
    def calendar_url(self) -> str:
        return self.base_url + ESCRIBE_CALENDAR

    def parse_calendar(self, body: bytes | str, first: date, last: date) -> list[dict[str, Any]]:
        """Calendar rows in the inclusive date range that carry a Video link."""
        try:
            payload = httpx.Response(200, content=body).json()
        except ValueError as exc:
            raise VideoError(f"the eScribe calendar did not answer JSON: {exc}") from exc
        rows = payload.get("d") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise VideoError("the eScribe calendar answered no d list")
        found: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            day = _meeting_day(row)
            if day is None or not first <= day <= last or video_link(row) is None:
                continue
            found.append({**row, "day": day})
        found.sort(key=lambda row: str(row.get("StartDate")), reverse=True)
        return found

    def player_url(self, row: dict[str, Any]) -> str:
        link = video_link(row)
        if link is None:
            raise VideoError("the eScribe meeting has no Video link")
        return urljoin(self.base_url + "/", str(link.get("Url") or ""))

    def player_media(self, url: str) -> tuple[str, str]:
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise VideoError(f"the eScribe player could not be reached: {exc}") from exc
        if response.status_code != 200:
            raise VideoError(f"the eScribe player answered HTTP {response.status_code} for {url}")
        client_id, file_name = parse_player(response.text)
        media = isilive_media_url(client_id, file_name)
        return media, media + ".vtt"

    def list_videos(self, first: date, last: date) -> list[VideoListing]:
        """Meetings in the range with a Video link, resolved to MP4 and VTT."""
        if first > last:
            raise ValueError("the first date must be on or before the last date")
        try:
            response = self.client.post(
                self.calendar_url,
                json={
                    "calendarStartDate": first.isoformat(),
                    "calendarEndDate": last.isoformat(),
                },
            )
        except httpx.HTTPError as exc:
            raise VideoError(f"the eScribe calendar could not be reached: {exc}") from exc
        if response.status_code != 200:
            raise VideoError(
                f"the eScribe calendar answered HTTP {response.status_code} for {self.calendar_url}"
            )
        rows = self.parse_calendar(response.content, first, last)

        videos: list[VideoListing] = []
        for row in rows:
            player_url = self.player_url(row)
            media_url, captions_url = self.player_media(player_url)
            meeting_id = str(row.get("ID") or "").strip()
            if not meeting_id:
                meeting_id = (parse_qs(urlsplit(player_url).query).get("Id") or [""])[0]
            videos.append(
                VideoListing(
                    video_id=meeting_id,
                    title=str(row.get("MeetingName") or "").strip(),
                    published=str(row.get("StartDate") or "").strip() or None,
                    channel_id=self.tenant,
                    source_method=SOURCE_ESCRIBE,
                    duration_s=_duration(row),
                    live_status="finished",
                    media_url=media_url,
                    captions_url=captions_url,
                )
            )
        return videos


def escribe_lister(
    client: httpx.Client,
    tenant: str = DEFAULT_TENANT,
    *,
    base_url: str | None = None,
) -> EScribeLister:
    """The lister for a real run."""
    return EScribeLister(client, tenant=tenant, base_url=base_url)
