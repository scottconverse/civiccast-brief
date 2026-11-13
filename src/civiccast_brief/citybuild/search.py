"""Find a city's meeting sources and bodies, with rules and web requests only.

The search reads the city's website (given, or guessed from the name and state) and a few of its
meeting pages, and sorts every link by platform (:mod:`civiccast_brief.citybuild.detect`). Then it
asks each platform it can read:

- PrimeGov lists its committees, which become the city's bodies.
- A YouTube link is turned into its channel id, and the channel's newest video titles say which
  bodies it carries.
- A Granicus archive page says which bodies it carries the same way.

Pages the editor adds ("Body | https://...") are for bodies with no platform, like a school board:
a page of dated documents, a page that links one page per meeting, or a Google Drive folder.

Platforms it finds but cannot read yet are listed, not saved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild import detect, drive, platforms
from civiccast_brief.citybuild.documents import document_links
from civiccast_brief.citybuild.fetch import Fetcher, FetchError, Page
from civiccast_brief.citybuild.page import Link, link_date, page_text, parse, squash
from civiccast_brief.citybuild.read import body_matches, committees, feed_entries
from civiccast_brief.sources import Source, SourceError, make_source

STATES: Final = {
    "AL": "alabama",
    "AK": "alaska",
    "AZ": "arizona",
    "AR": "arkansas",
    "CA": "california",
    "CO": "colorado",
    "CT": "connecticut",
    "DE": "delaware",
    "FL": "florida",
    "GA": "georgia",
    "HI": "hawaii",
    "ID": "idaho",
    "IL": "illinois",
    "IN": "indiana",
    "IA": "iowa",
    "KS": "kansas",
    "KY": "kentucky",
    "LA": "louisiana",
    "ME": "maine",
    "MD": "maryland",
    "MA": "massachusetts",
    "MI": "michigan",
    "MN": "minnesota",
    "MS": "mississippi",
    "MO": "missouri",
    "MT": "montana",
    "NE": "nebraska",
    "NV": "nevada",
    "NH": "newhampshire",
    "NJ": "newjersey",
    "NM": "newmexico",
    "NY": "newyork",
    "NC": "northcarolina",
    "ND": "northdakota",
    "OH": "ohio",
    "OK": "oklahoma",
    "OR": "oregon",
    "PA": "pennsylvania",
    "RI": "rhodeisland",
    "SC": "southcarolina",
    "SD": "southdakota",
    "TN": "tennessee",
    "TX": "texas",
    "UT": "utah",
    "VT": "vermont",
    "VA": "virginia",
    "WA": "washington",
    "WV": "westvirginia",
    "WI": "wisconsin",
    "WY": "wyoming",
    "DC": "dc",
}


def state_name(state: str) -> str:
    """The full name for a two-letter state code ("CO" gives "Colorado"); anything else as typed."""
    text = state.strip()
    slug = STATES.get(text.upper()) if len(text) == 2 else None
    if slug is None:
        return text
    if slug == "dc":
        return "District of Columbia"
    for first in ("new", "north", "south", "west", "rhode"):
        if slug.startswith(first) and slug != first:
            return f"{first.title()} {slug[len(first) :].title()}"
    return slug.title()


#: Body names a video title or archive page is checked for when no platform listed the bodies.
COMMON_BODIES: Final = (
    "City Council",
    "Town Council",
    "Town Board",
    "Board of Trustees",
    "Board of County Commissioners",
    "Planning and Zoning Commission",
    "Planning Commission",
    "Board of Education",
    "Board of Directors",
)
#: Words in a link's text that make it a page worth reading for meeting links.
KEYWORDS: Final = ("agenda", "meeting", "minutes", "board", "commission", "council", "video")
#: How many of the city site's own pages are read after the home page.
MAX_PAGES: Final = 8
_MAX_LINK_TEXT: Final = 60
_PLATFORM_URL: Final = re.compile(
    r"https?://[A-Za-z0-9.-]+\.(?:primegov\.com|legistar\.com|granicus\.com|civicclerk\.com|"
    r"escribemeetings\.com|boarddocs\.com|civicweb\.net|onboardmeetings\.com)[^\s\"'<>\\]*"
)
_DRIVE_FOLDER: Final = re.compile(
    r"drive\.google\.com/(?:drive/(?:u/\d+/)?folders/|embeddedfolderview\?id=)([A-Za-z0-9_-]{10,100})"
)
#: A folder link inside a page builder's JSON names its folder in the "title" just before it.
_JSON_TITLE: Final = re.compile(r'"title"\s*:\s*"([^"]{1,160})"')
_HANDLE_CHANNEL: Final = (
    re.compile(
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"'
    ),
    re.compile(r'"externalId":"(UC[A-Za-z0-9_-]{22})"'),
    re.compile(r'<meta itemprop="identifier" content="(UC[A-Za-z0-9_-]{22})"'),
)


@dataclass
class Candidate:
    """A source the program can read, ready for the editor to confirm."""

    source: Source
    how: str

    def to_json(self) -> dict[str, Any]:
        return {"source": self.source.to_json(), "how": self.how}


@dataclass
class Unread:
    """A meeting platform that was found but has no reader in this program."""

    platform: str
    url: str
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"platform": self.platform, "url": self.url, "note": self.note}


@dataclass
class Search:
    name: str
    state: str
    website: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    bodies: list[str] = field(default_factory=list)
    unread: list[Unread] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)

    def add(self, source: Source, how: str) -> None:
        for at, old in enumerate(self.candidates):
            if old.source.id == source.id:
                known = {b.casefold() for b in old.source.bodies}
                extra = tuple(b for b in source.bodies if b.casefold() not in known)
                if extra:
                    merged = make_source(
                        old.source.kind,
                        {**_fields(old.source), "bodies": list(old.source.bodies + extra)},
                    )
                    self.candidates[at] = Candidate(merged, old.how)
                break
        else:
            self.candidates.append(Candidate(source, how))
        self.note_bodies(source.bodies)

    def note_bodies(self, names: Iterable[str]) -> None:
        known = {b.casefold() for b in self.bodies}
        for name in names:
            if name.casefold() not in known:
                self.bodies.append(name)
                known.add(name.casefold())

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "website": self.website,
            "sources": [c.to_json() for c in self.candidates],
            "bodies": self.bodies,
            "found_not_read": [u.to_json() for u in self.unread],
            "notes": self.notes,
            "requests": len(self.asked),
        }

    def report(self) -> list[str]:
        out = [f"{self.name}, {self.state}: website {self.website or 'not found'}"]
        out.append(f"Sources the program can read ({len(self.candidates)}):")
        for c in self.candidates:
            out.append(f"  {c.source.kind:9} {c.source.address}")
            out.append(f"            bodies: {', '.join(c.source.bodies)}")
            out.append(f"            found: {c.how}")
        if self.unread:
            out.append(f"Found, no reader in this program yet ({len(self.unread)}):")
            out += [
                f"  {u.platform}: {u.url}" + (f" ({u.note})" if u.note else "") for u in self.unread
            ]
        if self.notes:
            out.append("Notes:")
            out += [f"  {n}" for n in self.notes]
        return out


def _fields(source: Source) -> dict[str, Any]:
    data = source.to_json()
    data.pop("id", None)
    return data


def slug_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def guesses(name: str, state: str) -> list[str]:
    n = slug_of(name)
    st = state.strip().casefold()
    full = STATES.get(state.strip().upper(), slug_of(state))
    if len(st) != 2:
        st = next((k.casefold() for k, v in STATES.items() if v == slug_of(state)), st)
    hosts = [
        f"{n}{full}.gov",
        f"{n}{st}.gov",
        f"cityof{n}.org",
        f"cityof{n}.gov",
        f"{n}.gov",
        f"{n}{st}.org",
        f"ci.{n}.{st}.us",
    ]
    return [f"https://www.{h}/" for h in hosts] + [f"https://{h}/" for h in hosts]


def search_city(
    name: str,
    state: str,
    *,
    website: str | None = None,
    extra: Iterable[tuple[str, str]] = (),
    fetcher: Fetcher | None = None,
    today: date | None = None,
    open_in_browser: Callable[[str], Page] | None = None,
) -> Search:
    fetcher = fetcher or Fetcher()
    today = today or date.today()
    result = Search(name=squash(name), state=squash(state))
    result.asked = fetcher.asked
    home = _find_website(result, website, fetcher, open_in_browser)
    found: dict[tuple[str, str, str], detect.Found] = {}
    if home is not None:
        for url, text in _crawl(home, fetcher, open_in_browser):
            for item in _platform_links(text, url):
                found.setdefault((item.platform, item.key.casefold(), item.extra.casefold()), item)
    platforms = list(found.values())
    _primegov(result, [f for f in platforms if f.platform == "primegov"], fetcher)
    if not any(f.platform in ("primegov", "legistar", "granicus") for f in platforms):
        _probe(result, fetcher)
    for item in platforms:
        if item.platform == "legistar":
            _legistar(result, item, fetcher)
    for item in platforms:
        if item.platform == "granicus":
            _granicus(result, item, fetcher, None)
    youtube = [f for f in platforms if f.platform == "youtube"]
    for item in youtube:
        _youtube(result, item, fetcher, None)
    for item in platforms:
        if item.platform in _LISTED:
            _LISTED[item.platform](result, item, fetcher, today, None)
        elif item.platform not in ("primegov", "legistar", "granicus", "youtube", "gdrive"):
            result.unread.append(Unread(item.label, item.url))
    for body, url in extra:
        _extra_page(result, squash(body), url.strip(), fetcher, today)
    return result


# ----- the city's own site ----------------------------------------------------------------------


def _read_site(
    url: str, fetcher: Fetcher, open_in_browser: Callable[[str], Page] | None
) -> tuple[Page | None, str]:
    """``url`` as a page, or None and why not. A site that answers the plain client HTTP 403 is
    opened in Chrome, as the board and member readers do."""
    try:
        return fetcher.get(url), ""
    except FetchError as exc:
        problem = str(exc)
    fetcher.log(f"  no: {problem}")
    if open_in_browser is None or "HTTP 403" not in problem:
        return None, problem
    try:
        return open_in_browser(url), ""
    except FetchError as exc:
        return None, f"{problem} and did not open in Chrome either: {exc}"


def _find_website(
    result: Search,
    website: str | None,
    fetcher: Fetcher,
    open_in_browser: Callable[[str], Page] | None = None,
) -> Page | None:
    if website:
        url = website.strip()
        if "://" not in url:
            url = f"https://{url}"
        page, problem = _read_site(url, fetcher, open_in_browser)
        if page is None:
            result.notes.append(f"the website could not be read: {problem}")
            return None
        result.website = page.url
        return page
    for url in guesses(result.name, result.state):
        page = fetcher.try_get(url)
        if page is not None and result.name.casefold() in page_text(page.text).casefold():
            result.website = page.url
            result.notes.append(f"website guessed from the name: {page.url}")
            return page
    result.notes.append("no website found from the name; give it with --website")
    return None


def _site_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _crawl(
    home: Page, fetcher: Fetcher, open_in_browser: Callable[[str], Page] | None = None
) -> list[tuple[str, str]]:
    pages = [(home.url, home.text)]
    domain = _site_domain(home.url)
    _, links, _ = parse(home.text, home.url)
    picked: list[str] = []
    for link in links:
        text = link.text.casefold()
        host = _site_domain(link.url)
        if not (host == domain or host.endswith("." + domain)):
            continue
        if detect.classify(link.url) or document_links([link]):
            continue
        if len(link.text) > _MAX_LINK_TEXT or not any(k in text for k in KEYWORDS):
            continue
        url = link.url.split("#", 1)[0]
        if url not in picked and url.rstrip("/") != home.url.rstrip("/"):
            picked.append(url)
    for url in picked[:MAX_PAGES]:
        page, _ = _read_site(url, fetcher, open_in_browser)
        if page is not None:
            pages.append((page.url, page.text))
    return pages


def _platform_links(text: str, base: str) -> list[detect.Found]:
    _, links, frames = parse(text, base)
    urls = [link.url for link in links] + frames
    urls += [m.group(0) for m in _PLATFORM_URL.finditer(text.replace("\\/", "/"))]
    found = []
    for url in urls:
        item = detect.classify(url)
        if item is not None:
            found.append(item)
    return found


# ----- PrimeGov and Legistar --------------------------------------------------------------------


def _primegov(result: Search, items: list[detect.Found], fetcher: Fetcher) -> None:
    for key in dict.fromkeys(item.key for item in items):
        base = f"https://{key}.primegov.com"
        try:
            rows = committees(base, fetcher)
        except (FetchError, ValueError) as exc:
            result.notes.append(f"PrimeGov {base} was linked but its committee list failed: {exc}")
            continue
        names = [name for _, name in rows]
        if not names:
            result.notes.append(f"PrimeGov {base} lists no committees")
            continue
        result.add(
            make_source("primegov", {"url": base, "bodies": names}),
            f"linked from the city site; the portal lists {len(names)} committees",
        )


def _probe(result: Search, fetcher: Fetcher) -> None:
    """No platform was linked: ask PrimeGov and Legistar whether the city's name is a tenant."""
    slug = slug_of(result.name)
    base = f"https://{slug}.primegov.com"
    try:
        rows = committees(base, fetcher)
    except (FetchError, ValueError):
        rows = []
    if rows:
        result.add(
            make_source("primegov", {"url": base, "bodies": [n for _, n in rows]}),
            f"PrimeGov answers for {slug}; check that it is this city's portal",
        )
    _legistar(result, detect.Found("legistar", f"https://{slug}.legistar.com", slug), fetcher, True)


#: Said of a platform the city's site does not link, found by trying the city's name as a tenant.
PROBED: Final = "not linked from the city site; a place of the same name elsewhere may own it"


def _legistar(result: Search, item: detect.Found, fetcher: Fetcher, probing: bool = False) -> None:
    if not item.key:
        result.unread.append(Unread("Legistar", item.url))
        return
    page = fetcher.try_get(f"https://webapi.legistar.com/v1/{item.key}/bodies")
    try:
        rows = page.json() if page is not None else None
    except ValueError:
        rows = None
    if not isinstance(rows, list) or not rows:
        if not probing:
            result.unread.append(
                Unread("Legistar", item.url, "its public body list did not answer")
            )
        return
    names = [
        squash(str(r.get("BodyName"))) for r in rows if isinstance(r, dict) and r.get("BodyName")
    ]
    shown = ", ".join(names[:8]) + (", ..." if len(names) > 8 else "")
    note = f"{len(names)} bodies: {shown}"
    result.unread.append(Unread("Legistar", item.url, f"{PROBED}; {note}" if probing else note))


# ----- Granicus and YouTube ---------------------------------------------------------------------


def _known_bodies(result: Search, given: str | None) -> list[str]:
    if given:
        return [given]
    return list(dict.fromkeys([*result.bodies, *COMMON_BODIES]))


def _granicus(result: Search, item: detect.Found, fetcher: Fetcher, body: str | None) -> None:
    if not item.extra:
        result.unread.append(Unread("Granicus", item.url, "no view_id in the link"))
        return
    host = urlsplit(item.url).hostname or ""
    page = fetcher.try_get(f"https://{host}/ViewPublisher.php?view_id={item.extra}")
    if page is None:
        result.unread.append(Unread("Granicus", item.url, "the archive page did not answer"))
        return
    text = page_text(page.text)
    bodies = [b for b in _known_bodies(result, body) if body_matches(b, text)]
    if not bodies:
        result.unread.append(Unread("Granicus", item.url, "the archive names none of the bodies"))
        return
    result.add(
        make_source("granicus", {"host": host, "view_id": item.extra, "bodies": bodies}),
        "linked from the city site; the archive page names these bodies",
    )


def channel_id(item: detect.Found, fetcher: Fetcher) -> str:
    if item.key:
        return item.key
    if item.extra.startswith("playlist:"):
        return ""
    page = fetcher.get(f"https://www.youtube.com/{item.extra}")
    for pattern in _HANDLE_CHANNEL:
        match = pattern.search(page.text)
        if match:
            return match[1]
    return ""


def _youtube(result: Search, item: detect.Found, fetcher: Fetcher, body: str | None) -> None:
    try:
        channel = channel_id(item, fetcher)
    except FetchError as exc:
        result.unread.append(Unread("YouTube", item.url, f"the channel page did not answer: {exc}"))
        return
    if not channel:
        note = (
            "a playlist, not a channel" if item.extra.startswith("playlist:") else "no channel id"
        )
        result.unread.append(Unread("YouTube", item.url, note))
        return
    seen = any(
        c.source.kind == "youtube" and c.source.channel_id == channel for c in result.candidates
    )
    if seen and body is None:
        return
    try:
        videos = feed_entries(channel, fetcher)
    except FetchError as exc:
        result.unread.append(Unread("YouTube", item.url, f"the channel feed did not answer: {exc}"))
        return
    titles = " | ".join(title for _, title, _ in videos)
    bodies = [b for b in _known_bodies(result, body) if body_matches(b, titles)]
    if not bodies:
        result.unread.append(
            Unread("YouTube", item.url, f"none of its {len(videos)} newest titles names a body")
        )
        return
    result.add(
        make_source("youtube", {"channel_id": channel, "bodies": bodies}),
        f"channel {channel}; its newest video titles name these bodies",
    )


# ----- pages the editor adds --------------------------------------------------------------------


def _extra_page(result: Search, body: str, url: str, fetcher: Fetcher, today: date) -> None:
    if not body or not url:
        result.notes.append(f"skipped {body!r} | {url!r}: give a body name and an address")
        return
    if "://" not in url:
        url = f"https://{url}"
    result.note_bodies([body])
    item = detect.classify(url)
    if item is not None and item.platform == "primegov":
        result.add(
            make_source("primegov", {"url": f"https://{item.key}.primegov.com", "bodies": [body]}),
            "given",
        )
        return
    if item is not None and item.platform == "youtube":
        _youtube(result, item, fetcher, body)
        return
    if item is not None and item.platform == "granicus":
        _granicus(result, item, fetcher, body)
        return
    if item is not None and item.platform == "gdrive":
        _drive_folder(result, body, [item.key], fetcher, today, "given")
        return
    if item is not None and item.platform in _LISTED:
        _LISTED[item.platform](result, item, fetcher, today, body)
        return
    if item is not None:
        result.unread.append(Unread(item.label, url, f"for {body}"))
        return
    page = fetcher.try_get(url)
    if page is None:
        result.notes.append(f"{body}: {url} could not be read")
        return
    _, links, _ = parse(page.text, page.url)
    dated = [(d, link) for link in document_links(links) if (d := link_date(link))]
    if dated:
        newest = max(d for d, _ in dated)
        result.add(
            _webpage(page.url, "", body),
            f"the page links {len(dated)} dated documents; the newest is dated {newest}",
        )
        return
    folders = _folders_on(page.text)
    if folders and _drive_folder(result, body, folders, fetcher, today, f"linked from {page.url}"):
        return
    follow, count, newest = _meeting_pages(links, page.url)
    if follow:
        result.add(
            _webpage(page.url, follow, body),
            f"the page links {count} dated meeting pages under {follow}; the newest is {newest}",
        )
        return
    platforms = [p for p in _platform_links(page.text, page.url) if p.platform != "gdrive"]
    for p in platforms:
        result.unread.append(Unread(p.label, p.url, f"for {body}, linked from {page.url}"))
    if not platforms:
        result.notes.append(
            f"{body}: {page.url} has no dated documents, Drive folders or meeting pages"
        )


def _webpage(url: str, follow: str, body: str) -> Source:
    try:
        return make_source("webpage", {"url": url, "follow": follow, "bodies": [body]})
    except SourceError as exc:  # pragma: no cover - the address was fetched, so it is valid
        raise FetchError(str(exc)) from exc


def _folders_on(text: str) -> list[tuple[str, str]]:
    """Drive folder ids in page order, each with the text written just before it."""
    raw = text.replace("\\/", "/").replace('\\"', '"')
    found: list[tuple[str, str]] = []
    last = 0
    for match in _DRIVE_FOLDER.finditer(raw):
        chunk = raw[last : match.start()]
        titles = _JSON_TITLE.findall(chunk)
        before = titles[-1] if titles else squash(re.sub(r"<[^>]+>", " ", chunk))[-160:]
        last = match.end()
        if match[1] not in (f for f, _ in found):
            found.append((match[1], before))
    return found


def _drive_folder(
    result: Search,
    body: str,
    folders: list[tuple[str, str]] | list[str],
    fetcher: Fetcher,
    today: date,
    how: str,
) -> bool:
    pairs = [(f, "") if isinstance(f, str) else f for f in folders]
    ranked = sorted(pairs, key=lambda pair: "agenda" not in pair[1].casefold())
    for folder, before in ranked[:3]:
        page = fetcher.try_get(drive.folder_view_url(folder))
        listed = drive.entries(page.text, today) if page is not None else []
        if not listed:
            continue
        label = f' ("{before[-60:]}")' if before else ""
        others = [f for f, _ in pairs if f != folder]
        note = f"; {len(others)} more folders on the page not used" if others else ""
        result.add(
            make_source("gdrive", {"folder_id": folder, "bodies": [body]}),
            f"Drive folder {how}{label}, {len(listed)} {'entry' if len(listed) == 1 else 'entries'}{note}",
        )
        return True
    return False


def _meeting_pages(links: list[Link], base: str) -> tuple[str, int, date | None]:
    """The path start most dated same-site links share: one page per meeting."""
    domain = _site_domain(base)
    groups: dict[str, list[date]] = {}
    for link in links:
        if _site_domain(link.url) != domain:
            continue
        day = link_date(link)
        path = urlsplit(link.url).path
        parts = [p for p in path.split("/") if p]
        if day is None or len(parts) < 2:
            continue
        groups.setdefault(f"/{parts[0]}/", []).append(day)
    if not groups:
        return "", 0, None
    follow, days = max(groups.items(), key=lambda pair: len(pair[1]))
    if len(days) < 2:
        return "", 0, None
    return follow, len(days), max(days)


# ----- CivicClerk, eScribe and CivicPlus --------------------------------------------------------


def _listed(
    result: Search,
    item: detect.Found,
    kind: str,
    fields: dict[str, str],
    names: list[str],
    body: str | None,
    how: str,
) -> None:
    if not names:
        result.unread.append(Unread(item.label, item.url, f"{how}, and names no body"))
        return
    bodies = [body] if body else names
    result.add(
        make_source(kind, {**fields, "bodies": bodies}),
        f"{'given' if body else 'linked from the city site'}; {how} {len(names)} bodies",
    )


def _civicclerk(
    result: Search, item: detect.Found, fetcher: Fetcher, today: date, body: str | None
) -> None:
    try:
        names = platforms.clerk_bodies(item.key, fetcher)
    except (FetchError, ValueError) as exc:
        result.unread.append(Unread(item.label, item.url, f"its body list did not answer: {exc}"))
        return
    _listed(result, item, "civicclerk", {"tenant": item.key}, names, body, "the portal lists")


def _escribe(
    result: Search, item: detect.Found, fetcher: Fetcher, today: date, body: str | None
) -> None:
    try:
        meetings = platforms.escribe_meetings(item.key, fetcher, today)
    except (FetchError, ValueError) as exc:
        result.unread.append(Unread(item.label, item.url, f"its calendar did not answer: {exc}"))
        return
    names = platforms.escribe_bodies(meetings)
    how = f"its calendar's last {platforms.ESCRIBE_DAYS} days show"
    _listed(result, item, "escribe", {"tenant": item.key}, names, body, how)


def _civicplus(
    result: Search, item: detect.Found, fetcher: Fetcher, today: date, body: str | None
) -> None:
    site = (urlsplit(item.url).hostname or item.key).lower()
    names: list[str] = []
    seen: list[str] = []
    for path, bodies_on in (
        ("/AgendaCenter", platforms.agenda_center_bodies),
        ("/Archive.aspx", platforms.archive_center_bodies),
    ):
        page = fetcher.try_get(f"https://{site}{path}")
        if page is None:
            continue
        site = (urlsplit(page.url).hostname or site).lower()
        found = bodies_on(page.text)
        if found:
            seen.append("Agenda Center" if path == "/AgendaCenter" else "Archive Center")
        known = {n.casefold() for n in names}
        names += [n for n in found if n.casefold() not in known]
    how = f"the {' and '.join(seen) or 'Agenda Center and Archive Center'} list"
    _listed(result, item, "civicplus", {"site": site}, names, body, how)


_LISTED: Final = {"civicclerk": _civicclerk, "escribe": _escribe, "civicplus": _civicplus}
