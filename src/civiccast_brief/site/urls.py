"""URLs for one site build.

The build takes a base URL such as https://longmont.civiccast.org. The base may carry a path
(https://example.org/brief), so every internal link is prefixed with that path. Feeds, the sitemap,
canonical links and share metadata always use the absolute form.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Urls:
    base: str
    """Absolute base with no trailing slash, for example https://longmont.civiccast.org."""

    @classmethod
    def from_base(cls, base_url: str) -> Urls:
        text = base_url.strip()
        parsed = urlparse(text)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"--base-url must be an absolute http or https address: {base_url!r}")
        if parsed.query or parsed.fragment:
            raise ValueError("--base-url must not carry a query string or fragment")
        return cls(base=text.rstrip("/"))

    @property
    def root(self) -> str:
        """The path part of the base: "" for a domain root, "/brief" for a sub-path."""
        return urlparse(self.base).path.rstrip("/")

    def href(self, path: str) -> str:
        """Root-relative link for a path that starts with a slash, such as /episodes/x/."""
        return f"{self.root}{path}"

    def absolute(self, path: str) -> str:
        """Absolute URL for a path that starts with a slash."""
        return f"{self.base}{path}"

    def resolve(self, url: str) -> str:
        """Turn a URL from the input into an absolute one. A path starting with / is site-relative."""
        if url.startswith("/") and not url.startswith("//"):
            return self.absolute(url)
        return url

    def page_path(self, *parts: str) -> str:
        """Directory-style page path: page_path("episodes", "x") -> /episodes/x/."""
        clean = [p.strip("/") for p in parts if p.strip("/")]
        return "/" + "/".join(clean) + "/" if clean else "/"

    def is_site_url(self, url: str) -> bool:
        """True for an absolute URL under this base, or a path relative to the site root."""
        if url.startswith("/") and not url.startswith("//"):
            return True
        return url == self.base or url.startswith(self.base + "/")


def timestamp_link(video_url: str | None, seconds: float) -> str | None:
    """Link to a meeting video at a time. YouTube takes ?t=; other hosts get a #t= fragment."""
    if not video_url:
        return None
    whole = int(seconds)
    host = urlparse(video_url).netloc.lower()
    if host.endswith(("youtube.com", "youtu.be")):
        separator = "&" if urlparse(video_url).query else "?"
        return f"{video_url}{separator}t={whole}s"
    return f"{video_url}#t={whole}"
