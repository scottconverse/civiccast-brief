"""List CivicClerk events whose public media endpoint exposes a direct MP4.

Erie is the first configured town. The API host is tenant-shaped
(``https://<tenant>.api.civicclerk.com/v1``), so a caller can point the same
reader at another CivicClerk town without changing code.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from civiccast_brief.record.video.youtube.listing import (
    SOURCE_CIVICCLERK,
    VideoError,
    VideoListing,
)

CIVICCLERK_API = "https://{tenant}.api.civicclerk.com/v1"
DEFAULT_TENANT = "erieco"
DEFAULT_TOP = 50
MAX_PAGES = 100
#: Answers that say the API failed briefly, worth asking again (Nov. 22 2026: Superior's
#: Events query answered 500 twice, then 200 a minute later).
RETRY_STATUS: frozenset[int] = frozenset({500, 502, 503, 504})
#: The waits between the three attempts. A connect or read timeout reads the same way.
RETRY_WAITS_S: tuple[float, ...] = (5.0, 15.0)


def civicclerk_api(tenant: str) -> str:
    """The API root for one CivicClerk tenant."""
    return CIVICCLERK_API.format(tenant=tenant.strip())


def events_url(base_url: str, first: date, last: date, *, top: int = DEFAULT_TOP) -> str:
    """The OData Events URL for an inclusive local-date window."""
    root = base_url.rstrip("/")
    start = f"{first.isoformat()}T00:00:00Z"
    end = f"{last.isoformat()}T23:59:59Z"
    return str(
        httpx.URL(
            f"{root}/Events",
            params={
                "$filter": f"startDateTime ge {start} and startDateTime le {end}",
                "$orderby": "startDateTime desc",
                "$top": str(top),
            },
        )
    )


def events_media_url(base_url: str, event_id: object) -> str:
    return f"{base_url.rstrip('/')}/EventsMedia/{event_id}"


def _get(client: httpx.Client, url: str, sleep: Callable[[float], None]) -> httpx.Response:
    """GET ``url`` again after a brief server failure, up to three attempts.

    A 500, 502, 503 or 504, or a connect or read timeout, is waited out (5 s, then 15 s) and
    asked again. The last failure comes back as it is: a bad status as the response, a timeout
    as the exception, so the caller reports it the way it did before any retry.
    """
    attempts = len(RETRY_WAITS_S) + 1
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url)
        except (httpx.ConnectTimeout, httpx.ReadTimeout):
            if attempt == attempts:
                raise
            sleep(RETRY_WAITS_S[attempt - 1])
            continue
        if response.status_code in RETRY_STATUS and attempt < attempts:
            sleep(RETRY_WAITS_S[attempt - 1])
            continue
        return response
    raise AssertionError("unreachable: the loop returns or raises on its last attempt")


def _json_object(response: httpx.Response, what: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise VideoError(f"{what} answered HTTP {response.status_code} for {response.url}")
    try:
        data = response.json()
    except ValueError as exc:
        raise VideoError(f"{what} did not answer JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VideoError(f"{what} did not answer a JSON object")
    return data


def _day(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _duration(row: dict[str, Any]) -> int | None:
    hours = row.get("durationHrs")
    minutes = row.get("durationMin")
    if isinstance(hours, bool) or isinstance(minutes, bool):
        return None
    if not isinstance(hours, (int, float)) or not isinstance(minutes, (int, float)):
        return None
    return int(hours * 3600 + minutes * 60)


def _direct_mp4(value: object) -> str | None:
    url = str(value or "").strip()
    if not url:
        return None
    return url if urlsplit(url).path.casefold().endswith(".mp4") else None


class CivicClerkLister:
    """Reads CivicClerk events over an :class:`httpx.Client` the caller owns."""

    def __init__(
        self,
        client: httpx.Client,
        tenant: str = DEFAULT_TENANT,
        *,
        base_url: str | None = None,
        top: int = DEFAULT_TOP,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.tenant = tenant.strip()
        self.base_url = (base_url or civicclerk_api(self.tenant)).rstrip("/")
        self.top = top
        self._sleep = sleep

    def list_videos(self, first: date, last: date) -> list[VideoListing]:
        """Events in the inclusive date range with a non-empty direct-MP4 videoUrl."""
        if first > last:
            raise ValueError("the first date must be on or before the last date")

        url: str | None = events_url(self.base_url, first, last, top=self.top)
        seen: set[str] = set()
        videos: list[VideoListing] = []
        while url and url not in seen and len(seen) < MAX_PAGES:
            seen.add(url)
            body = _json_object(_get(self.client, url, self._sleep), "the CivicClerk Events API")
            value = body.get("value")
            if not isinstance(value, list):
                raise VideoError("the CivicClerk Events API answered no value list")
            for row in value:
                if not isinstance(row, dict):
                    continue
                day = _day(row.get("startDateTime"))
                if day is None or not first <= day <= last:
                    continue
                event_id = row.get("id")
                if event_id is None:
                    continue
                media = _json_object(
                    _get(self.client, events_media_url(self.base_url, event_id), self._sleep),
                    f"the CivicClerk media API for event {event_id}",
                )
                media_url = _direct_mp4(media.get("videoUrl"))
                if media_url is None:
                    continue
                captions = str(media.get("closedCaptionUrl") or "").strip() or None
                if captions:
                    captions = urljoin(self.base_url + "/", captions)
                videos.append(
                    VideoListing(
                        video_id=str(event_id),
                        title=str(row.get("eventName") or "").strip(),
                        published=str(row.get("startDateTime") or "").strip() or None,
                        channel_id=str(row.get("categoryName") or self.tenant).strip(),
                        source_method=SOURCE_CIVICCLERK,
                        duration_s=_duration(row),
                        live_status="finished",
                        media_url=media_url,
                        captions_url=captions,
                    )
                )
            next_link = body.get("@odata.nextLink")
            url = urljoin(self.base_url + "/", str(next_link)) if next_link else None
        return videos


def civicclerk_lister(
    client: httpx.Client,
    tenant: str = DEFAULT_TENANT,
    *,
    base_url: str | None = None,
    top: int = DEFAULT_TOP,
) -> CivicClerkLister:
    """The lister for a real run."""
    return CivicClerkLister(client, tenant=tenant, base_url=base_url, top=top)
