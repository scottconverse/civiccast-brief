"""OneDrive share links (``https://1drv.ms/...``), read in Chrome with rules and no model.

A town that keeps its minutes in OneDrive links each one as ``https://1drv.ms/<kind>/c/<owner>/
<id>?e=...`` (Jamestown, Colorado, checked live on 2026-11-11). The share opens only in a browser:
the old ``api.onedrive.com/v1.0/shares`` API refuses it.

* A Word, Excel or PowerPoint share (``/w/``, ``/x/``, ``/p/``) downloads the file when
  ``download=1`` is added to the link.
* A PDF share (``/b/``) opens in a viewer that asks
  ``.../_api/v2.0/drives/.../items/...?select=id,@content.downloadUrl``. The answer's
  ``@content.downloadUrl`` is on ``my.microsoftpersonalcontent.com``, which refuses it (401); the
  same path and query on ``onedrive.live.com`` downloads the PDF.
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild.browser import Browser
from civiccast_brief.citybuild.fetch import FetchError, Page

HOST: Final = "1drv.ms"
#: Part of the address of the viewer's question for a PDF's download address.
ITEM: Final = "content.downloadUrl"
DOWNLOAD_HOST: Final = "onedrive.live.com"


def is_share(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").lower() == HOST and len(parts.path.strip("/").split("/")) >= 2


def _kind(url: str) -> str:
    return urlsplit(url).path.strip("/").split("/")[0].lower()


def download_link(url: str) -> str:
    """The share link with ``download=1``, which downloads an Office file."""
    return url + ("&" if "?" in url else "?") + "download=1"


def pdf_link(item: Any, url: str) -> str:
    """Where a PDF share downloads, from the viewer's answer for its item."""
    where = item.get("@content.downloadUrl") if isinstance(item, dict) else None
    if not isinstance(where, str) or not where:
        raise FetchError(f"{url}: the viewer gave no download address for the PDF")
    parts = urlsplit(where)
    return f"https://{DOWNLOAD_HOST}{parts.path}?{parts.query}"


class Reader:
    """The browser, reading OneDrive share links; any other address is the browser's to read."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser

    def get(self, url: str) -> Page:
        if not is_share(url):
            return self.browser.get(url)
        if _kind(url) == "b":
            item = self.browser.open_and_catch(url, ITEM)
            page = self.browser.get(pdf_link(item, url))
        else:
            page = self.browser.get(download_link(url))
        return Page(
            url=url, status=page.status, content=page.content, content_type=page.content_type
        )
