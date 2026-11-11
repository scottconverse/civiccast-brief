"""The one way the city search asks the web: a named, size-limited GET (or, for a meeting
calendar that is only asked that way, a JSON POST).

Every request follows at most four
redirects and reads at most a few megabytes. Requests to YouTube take their turn in the process
pace (:mod:`civiccast_brief.record.pacing`), and a 429 from YouTube holds every later YouTube
request instead of asking again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from civiccast_brief.record import pacing
from civiccast_brief.record.adapters.primegov import MAX_REDIRECTS, decode_html

TIMEOUT_S = 30.0
MAX_BYTES = 8_000_000
_YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com")


class FetchError(RuntimeError):
    """A page could not be read. The message says which and why."""


@dataclass(frozen=True)
class Page:
    url: str
    status: int
    content: bytes
    content_type: str

    @property
    def text(self) -> str:
        return decode_html(self.content, self.content_type)

    def json(self) -> Any:
        import json

        return json.loads(self.content.decode("utf-8-sig"))


@dataclass
class Fetcher:
    """GETs over one :class:`httpx.Client`. ``log`` hears one line per request."""

    client: httpx.Client | None = None
    log: Callable[[str], None] = lambda _line: None
    max_bytes: int = MAX_BYTES
    asked: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx.Client(
                timeout=TIMEOUT_S,
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
            )

    def get(self, url: str) -> Page:
        """GET ``url``. Any answer but 200 is a :class:`FetchError`."""
        host = (urlsplit(url).hostname or "").lower()
        youtube = host in _YOUTUBE_HOSTS
        if youtube:
            try:
                pacing.pacer().wait(what="the city search")
            except pacing.PaceHeld as exc:
                raise FetchError(f"{url}: {exc}") from exc
        return self._send("GET", url, youtube=youtube)

    def post_json(self, url: str, data: Any) -> Page:
        """POST ``data`` as JSON to ``url``, for a meeting calendar that is asked that way."""
        return self._send("POST", url, data=data)

    def _send(self, method: str, url: str, *, youtube: bool = False, data: Any = None) -> Page:
        self.asked.append(url)
        self.log(f"{method} {url}")
        assert self.client is not None
        extra: dict[str, Any] = {} if data is None else {"json": data}
        try:
            with self.client.stream(method, url, **extra) as response:
                if youtube and response.status_code == pacing.RATE_LIMIT_STATUS:
                    pacing.pacer().rate_limited(what="the city search", marker="HTTP 429")
                    raise FetchError(f"{url}: YouTube answered 429; YouTube requests now wait")
                if response.status_code != 200:
                    raise FetchError(f"{url} answered HTTP {response.status_code}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise FetchError(f"{url} is larger than {self.max_bytes} bytes")
                    chunks.append(chunk)
                return Page(
                    url=str(response.url),
                    status=response.status_code,
                    content=b"".join(chunks),
                    content_type=response.headers.get("content-type", ""),
                )
        except httpx.HTTPError as exc:
            raise FetchError(f"{url} could not be reached: {exc}") from exc

    def try_get(self, url: str) -> Page | None:
        """GET ``url``, or None when it cannot be read."""
        try:
            return self.get(url)
        except FetchError as exc:
            self.log(f"  no: {exc}")
            return None
