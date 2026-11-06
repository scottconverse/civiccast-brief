"""Walk a built site and check that every internal link resolves to a file.

Checked: href, src, action and poster attributes in every HTML page, and the link, guid, loc, url
and href values in sitemap.xml and feed.xml. An internal link is one under the base URL, or a
path starting with a slash. Each internal link must map to a file in the output folder. A
directory link (ending in /) maps to its index.html. A fragment (#id) on an HTML target must match
an id or a named anchor on that page. External links are counted, not fetched, so the check runs
without a network.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from civiccast_brief.site.urls import Urls

URL_ATTRIBUTES = {"href", "src", "action", "poster"}
SKIP_SCHEMES = {"mailto", "tel", "data"}


@dataclass
class LinkReport:
    pages: int = 0
    xml_files: int = 0
    links: int = 0
    internal: int = 0
    external: int = 0
    skipped: int = 0
    fragments_checked: int = 0
    broken: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.broken


class _PageScan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[tuple[str, str]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name == "id":
                self.ids.add(value)
            if name == "name" and tag == "a":
                self.ids.add(value)
            if name in URL_ATTRIBUTES:
                self.urls.append((name, value))


def _scheme(url: str) -> str:
    return urlsplit(url).scheme.lower()


def _html_ids(path: Path) -> set[str]:
    scan = _PageScan()
    scan.feed(path.read_text(encoding="utf-8"))
    return scan.ids


def _map_to_file(out_dir: Path, site_path: str) -> Path | None:
    """Map a site path such as /episodes/x/ to a file under out_dir, or None when none exists."""
    clean = unquote(site_path)
    relative = clean.lstrip("/")
    if clean.endswith("/") or relative == "":
        candidate = out_dir / relative / "index.html"
    else:
        candidate = out_dir / relative
    if candidate.is_file():
        return candidate
    if not clean.endswith("/") and (out_dir / relative / "index.html").is_file():
        return out_dir / relative / "index.html"
    return None


def _check_url(
    *,
    out_dir: Path,
    urls: Urls,
    source: str,
    page_path: str,
    raw: str,
    report: LinkReport,
    fragments_for: dict[Path, set[str]],
) -> None:
    value = raw.strip()
    report.links += 1
    if not value or value == "#":
        report.skipped += 1
        return
    scheme = _scheme(value)
    if scheme in SKIP_SCHEMES:
        report.skipped += 1
        return
    if scheme == "javascript":
        report.broken.append(f"{source}: javascript: link {value!r}")
        return
    if scheme in ("http", "https"):
        if not urls.is_site_url(value):
            report.external += 1
            return
        site_path = value[len(urls.base) :] or "/"
    elif value.startswith("/") and not value.startswith("//"):
        if urls.root and not (value == urls.root or value.startswith(urls.root + "/")):
            report.broken.append(f"{source}: {value!r} is outside the site base path {urls.root}")
            return
        site_path = value[len(urls.root) :] or "/"
    else:
        absolute = urljoin(urls.absolute(page_path), value)
        if not urls.is_site_url(absolute):
            report.external += 1
            return
        site_path = absolute[len(urls.base) :] or "/"

    path_only, _, fragment = site_path.partition("#")
    path_only = path_only.split("?", 1)[0]
    if not path_only.startswith("/"):
        path_only = "/" + path_only

    target = _map_to_file(out_dir, path_only)
    if target is None:
        report.broken.append(f"{source}: {value!r} -> no file for {path_only}")
        return
    report.internal += 1
    if fragment and target.suffix == ".html":
        report.fragments_checked += 1
        if target not in fragments_for:
            fragments_for[target] = _html_ids(target)
        if unquote(fragment) not in fragments_for[target]:
            report.broken.append(f"{source}: {value!r} -> no #{fragment} on {path_only}")


def _page_path_of(out_dir: Path, file: Path) -> str:
    rel = file.relative_to(out_dir).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def _xml_urls(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    found: list[str] = []
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local in ("loc", "link", "url") and element.text and element.text.strip():
            found.append(element.text.strip())
        if local == "guid" and element.get("isPermaLink") == "true" and element.text:
            found.append(element.text.strip())
        for name, value in element.attrib.items():
            key = name.rsplit("}", 1)[-1]
            if key in ("url", "href", "src") and value:
                found.append(value)
    return found


def check_site(out_dir: Path, base_url: str) -> LinkReport:
    """Check every internal link in a built site folder."""
    urls = Urls.from_base(base_url)
    report = LinkReport()
    fragments_for: dict[Path, set[str]] = {}
    for file in sorted(out_dir.rglob("*.html")):
        report.pages += 1
        scan = _PageScan()
        scan.feed(file.read_text(encoding="utf-8"))
        page_path = _page_path_of(out_dir, file)
        source = file.relative_to(out_dir).as_posix()
        for _, raw in scan.urls:
            _check_url(
                out_dir=out_dir,
                urls=urls,
                source=source,
                page_path=page_path,
                raw=raw,
                report=report,
                fragments_for=fragments_for,
            )
    for name in ("sitemap.xml", "feed.xml"):
        xml_file = out_dir / name
        if not xml_file.is_file():
            continue
        report.xml_files += 1
        for raw in _xml_urls(xml_file):
            _check_url(
                out_dir=out_dir,
                urls=urls,
                source=name,
                page_path="/",
                raw=raw,
                report=report,
                fragments_for=fragments_for,
            )
    return report
