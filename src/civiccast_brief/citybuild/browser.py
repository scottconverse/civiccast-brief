"""Read public pages that turn away a plain program, in the installed Google Chrome.

Some city record sites answer a plain HTTP client with 403 but open for anyone in a browser
(Louisville's Laserfiche WebLink, checked on 2026-11-11). This opens the Google Chrome installed
on this computer, in a window you can see, as it is: no stealth patches, no changed browser name,
no answering of challenges. It opens pages and downloads files the way a person's browser does. If
a site still stops Chrome, the read fails and says so.

It needs Playwright (``pip install playwright``) and Google Chrome. Playwright drives the
installed Chrome (``channel="chrome"``); it does not download a browser of its own.
"""

from __future__ import annotations

import atexit
import json
import time
from pathlib import Path
from typing import Any, Final, Protocol

from civiccast_brief.citybuild.fetch import FetchError, Page

#: The largest file read. Meeting packets run large (Louisville's reach 80 MB); only their first
#: pages are read.
MAX_BYTES: Final = 150_000_000
TIMEOUT_MS: Final = 60_000
#: Seconds between requests to a site, so the reader asks no faster than a person clicks.
PAUSE_S: Final = 1.0
_CHALLENGE: Final = ("just a moment", "attention required", "cf-chl")

#: A link that downloads ``url``, clicked the way a person clicks a document's link. Louisville's
#: site answers a page's script asking for the file with a challenge, and a click with the file.
_CLICK_JS: Final = """(url) => {
  const a = document.createElement('a');
  a.href = url; a.download = ''; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
}"""
_POST_JS: Final = """async ([url, data, headers]) => {
  const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
    headers: Object.assign({'Content-Type': 'application/json'}, headers),
    body: JSON.stringify(data)});
  return {status: r.status, type: r.headers.get('content-type') || '', text: await r.text()};
}"""


class Browser(Protocol):
    """What a reader asks of the browser. Tests give a fake one."""

    def open_and_catch(self, url: str, part: str) -> Any:
        """Open ``url`` and return the JSON of the first response whose address has ``part``."""
        ...

    def post_json(self, url: str, data: Any, headers: dict[str, str] | None = None) -> Any:
        """POST ``data`` as JSON from the open page and return the JSON answer."""
        ...

    def get(self, url: str) -> Page:
        """Download ``url`` from the open page, as a click on its link, and return the file."""
        ...


def challenged(status: int, text: str) -> bool:
    """A challenge page ("Just a moment...") in place of the page or file asked for."""
    lowered = text[:2000].casefold()
    return status in (403, 429, 503) and any(word in lowered for word in _CHALLENGE)


def kind_of_file(content: bytes) -> str:
    """The content type of a downloaded file, which a download does not report."""
    if content.startswith(b"%PDF"):
        return "application/pdf"
    if content.lstrip()[:1] == b"<":
        return "text/html"
    return "application/octet-stream"


class Chrome:
    """The installed Google Chrome, opened on first use and closed by :meth:`close`."""

    def __init__(self, log: Any = None) -> None:
        self._log = log or (lambda _line: None)
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._last = 0.0

    def _open(self) -> Any:
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError as exc:
            raise FetchError(
                "this site opens only in a browser; install Playwright (pip install playwright) "
                "and Google Chrome"
            ) from exc
        self._playwright = sync_playwright().start()
        try:
            # headless=False: the site refuses headless Chrome, which names itself HeadlessChrome.
            self._browser = self._playwright.chromium.launch(channel="chrome", headless=False)
        except Error as exc:
            self.close()
            raise FetchError(f"Google Chrome could not be opened: {exc}") from exc
        self._page = self._browser.new_page()
        self._page.set_default_timeout(TIMEOUT_MS)
        self._log("opened Google Chrome to read pages that open only in a browser")
        return self._page

    def _wait_turn(self) -> None:
        gap = PAUSE_S - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def open_and_catch(self, url: str, part: str) -> Any:
        from playwright.sync_api import Error

        page = self._open()
        self._wait_turn()
        try:
            with page.expect_response(lambda r: part in r.url, timeout=TIMEOUT_MS) as caught:
                page.goto(url, wait_until="domcontentloaded")
            response = caught.value
            text = response.text()
        except Error as exc:
            raise FetchError(f"{url} did not open in Chrome: {exc}") from exc
        if challenged(response.status, text):
            raise FetchError(f"{url}: the site stopped Chrome with a challenge page")
        if response.status != 200:
            raise FetchError(f"{url}: the page's list answered {response.status} in Chrome")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise FetchError(f"{url}: the page's list is not JSON") from exc

    def post_json(self, url: str, data: Any, headers: dict[str, str] | None = None) -> Any:
        from playwright.sync_api import Error

        page = self._open()
        self._wait_turn()
        try:
            out = page.evaluate(_POST_JS, [url, data, headers or {}])
        except Error as exc:
            raise FetchError(f"{url} could not be asked from Chrome: {exc}") from exc
        if challenged(out["status"], out["text"]):
            raise FetchError(f"{url}: the site stopped Chrome with a challenge page")
        if out["status"] != 200:
            raise FetchError(f"{url} answered {out['status']} in Chrome")
        try:
            return json.loads(out["text"])
        except ValueError as exc:
            raise FetchError(f"{url} did not answer JSON") from exc

    def get(self, url: str) -> Page:
        from playwright.sync_api import Error

        page = self._open()
        self._wait_turn()
        try:
            with page.expect_download(timeout=TIMEOUT_MS) as caught:
                page.evaluate(_CLICK_JS, url)
            download = caught.value
            failure = download.failure()
            if failure:
                raise FetchError(f"{url} did not download in Chrome: {failure}")
            path = Path(download.path())
            size = path.stat().st_size
            content = path.read_bytes() if size <= MAX_BYTES else b""
            download.delete()
        except Error as exc:
            raise FetchError(f"{url} could not be read in Chrome: {exc}") from exc
        if size > MAX_BYTES:
            raise FetchError(f"{url} is larger than {MAX_BYTES} bytes")
        kind = kind_of_file(content)
        if kind == "text/html" and challenged(403, content.decode("utf-8", errors="replace")):
            raise FetchError(f"{url}: the site stopped Chrome with a challenge page")
        return Page(url=url, status=200, content=content, content_type=kind)

    def close(self) -> None:
        # Closing must not hide the run's own result, so a failure to close is only logged.
        for thing, how in ((self._browser, "close"), (self._playwright, "stop")):
            if thing is None:
                continue
            try:
                getattr(thing, how)()
            except Exception as exc:  # noqa: BLE001
                self._log(f"Chrome did not {how} cleanly: {exc}")
        self._playwright = self._browser = self._page = None


_shared: Browser | None = None


def shared() -> Browser:
    """The one browser this run reads with, opened when a reader first asks for a page."""
    global _shared
    if _shared is None:
        _shared = Chrome()
        atexit.register(close_shared)
    return _shared


def use(browser: Browser | None) -> None:
    """Read with ``browser`` from now on (tests give a fake one); ``None`` goes back to Chrome."""
    global _shared
    _shared = browser


def close_shared() -> None:
    """Close Chrome if this run opened it. A browser given by :func:`use` is left as it is."""
    global _shared
    if isinstance(_shared, Chrome):
        _shared.close()
        _shared = None
