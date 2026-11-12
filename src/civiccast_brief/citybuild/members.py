"""Find a city's council members on its own website, by rule, for ``roster.json``.

The roster is the only source of speaker names, so it lists each member exactly as the city's page
prints the name. This reads that page the way a person would: it follows the site's links to the
governing body's page (City Council, Town Council, Board of Trustees, County Commissioners) and
reads the lines that pair a person's name with a seat title. No model is asked.

A page prints a member in one of these ways (each seen on a Boulder County town's site,
2026-11-11):

* ``Mark Lacis, Mayor`` or ``Marta Loachamin – County Commissioner`` (name, then title);
* ``Councilmember Caleb Dickinson, Ward I`` or ``Mayor: Michael Box`` (title, then name);
* ``Billy Giblin`` on one line and ``Trustee/Liaison to ...`` on the next;
* a name alone in a list of members whose other lines carry titles (``Leigh Williams`` under
  ``Mark Browning , Mayor``). The page prints no title for that name, so the member gets the
  body's member title (``Trustee``, ``Council Member``, ``Commissioner``) and the roster's note
  says which names those are.

The page with the most members wins, and a page that names a mayor or commissioner wins over one
that does not, so a library board's trustee list is never taken for the town board.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild.fetch import Fetcher, FetchError, Page
from civiccast_brief.citybuild.page import parse

#: The governing bodies, in the order one is picked when a city lists more than one.
GOVERNING: Final = (
    "City Council",
    "Town Council",
    "Board of Trustees",
    "Town Board",
    "Board of County Commissioners",
    "County Commissioners",
    "City Commission",
)
#: Pages read at most, per city.
MAX_PAGES: Final = 16
#: Fewer members than this is not a roster (a news item naming the mayor, say).
MIN_MEMBERS: Final = 3
#: Seconds between page reads, so a small town's site is not asked faster than a person clicks
#: (Jamestown's answered 429 to quick requests on 2026-11-11).
PAUSE_S: Final = 1.5
#: Waits before asking again a page that answered HTTP 429 (Jamestown did, 2026-11-11).
RETRY_S: Final = (5.0, 15.0)

_ROLE = (
    r"(?:Mayor\s+Pro[\s-]*Tem(?:pore)?|Deputy\s+Mayor|Mayor"
    r"|(?:City\s+|Town\s+)?Council\s*(?:Member|member|woman|man|or|lor)"
    r"|Council\s+Representative|At[\s-]Large\s+Representative|Ward\s+\w+\s+Representative"
    r"|Trustee|(?:County\s+)?Commissioner)"
)
_SEAT = r"(?:Ward\s+[\w]+|District\s+[\w]+|At[\s-]Large|Seat\s+\w+)"
_PARTICLES: Final = frozenset({"de", "del", "la", "van", "von", "da", "di", "le", "y"})
_SUFFIXES: Final = frozenset({"jr.", "sr.", "ii", "iii", "iv", "jr", "sr"})
#: Words that are never part of a person's name on these pages.
_NOT_NAME: Final = frozenset(
    [
        "mayor",
        "council",
        "board",
        "town",
        "city",
        "county",
        "commissioner",
        "commissioners",
        "trustee",
        "trustees",
        "member",
        "members",
        "contact",
        "email",
        "staff",
        "home",
        "government",
        "meeting",
        "meetings",
        "agenda",
        "agendas",
        "minutes",
        "calendar",
        "news",
        "view",
        "more",
        "office",
        "department",
        "committee",
        "commission",
        "services",
        "district",
        "ward",
        "the",
        "and",
        "of",
        "for",
        "to",
        "us",
        "at",
        "our",
        "your",
        "about",
        "open",
        "position",
        "vacant",
        "vacancy",
        "map",
        "term",
        "terms",
        "years",
        "page",
        "clerk",
        "manager",
        "administrator",
        "liaison",
        "pro",
        "tem",
    ]
)
_ROLE_RE: Final = re.compile(rf"^{_ROLE}$", re.IGNORECASE)
_TITLE_THEN_NAME: Final = re.compile(rf"^(?P<role>{_ROLE})\s*:?\s+(?P<name>.+?)$", re.IGNORECASE)
_NAME_THEN_TITLE: Final = re.compile(
    rf"^(?P<name>.+?)\s*(?:,|\s[–—-])\s*(?P<role>{_ROLE})(?:\s*(?:,|/|\s[–—-]).*)?$",
    re.IGNORECASE,
)
_ROLE_FIRST: Final = re.compile(rf"^(?P<role>{_ROLE})(?:\s*(?:[,/–—(-]|$).*)?$", re.IGNORECASE)
_SEAT_TAIL: Final = re.compile(rf"\s*,\s*(?P<seat>{_SEAT})$", re.IGNORECASE)
_CONTACT: Final = re.compile(r"\s*(?:\||\(\d{3}\)|\d{3}[-.]\d{3}[-.]\d{4}|\S+@\S+).*$")
_BLOCK_TAGS: Final = re.compile(
    r"</?(?:p|div|li|ul|ol|h[1-6]|br|tr|td|th|table|section|article|figcaption|dt|dd|header|"
    r"footer|nav|main|aside)\b[^>]*>",
    re.IGNORECASE,
)
#: Link words that lead toward the members page, with how strongly.
_LEADS: Final = (
    (re.compile(r"meet the|members|elected officials|mayor (?:and|&) council", re.IGNORECASE), 4),
    (
        re.compile(
            r"city council|town council|board of trustees|town board|commissioners", re.IGNORECASE
        ),
        3,
    ),
    (re.compile(r"\bcouncil\b|\btrustees\b|\bmayor\b", re.IGNORECASE), 2),
    (re.compile(r"government", re.IGNORECASE), 1),
)
#: Links that lead to meetings, records or other boards, never to the members.
_AWAY: Final = re.compile(
    r"agenda|minutes|calendar|meeting|video|recording|news|alert|election|apply|vacanc|"
    r"advisory|planning|library|youth|historic|adjustment|equalization|bond|packet|"
    r"\bcommission\b|goals|handbook|strategic|liaison|contact|form|document|pdf|budget|"
    r"mayors of|past",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Member:
    name: str
    first: str
    last: str
    role: str

    def to_json(self) -> dict[str, str]:
        return {"name": self.name, "first": self.first, "last": self.last, "role": self.role}


@dataclass
class Found:
    """The members found on one page, or why none were."""

    members: list[Member] = field(default_factory=list)
    page: str = ""
    untitled: list[str] = field(default_factory=list)  # names the page printed with no title
    body: str = ""
    problem: str = ""
    asked: list[str] = field(default_factory=list)
    #: Other pages on the site that list as many members with different names: (page, names
    #: only that page lists).
    differ: list[tuple[str, list[str]]] = field(default_factory=list)


def governing_body(bodies: list[str]) -> str | None:
    """The city's governing body among ``bodies``, or None when none is listed."""
    for wanted in GOVERNING:
        for body in bodies:
            if body.casefold() == wanted.casefold():
                return body
    return None


def page_lines(text: str) -> list[str]:
    """The page's text, one line per block (paragraph, list item, cell, heading, line break)."""
    text = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = _BLOCK_TAGS.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return [
        line for line in (" ".join(html.unescape(raw).split()) for raw in text.split("\n")) if line
    ]


def _word_ok(word: str) -> bool:
    if word.casefold() in _PARTICLES:
        return True
    if re.fullmatch(r"[A-Z]\.", word):  # a middle initial
        return True
    if word.casefold() in _SUFFIXES:
        return True
    if not re.fullmatch(r"[A-Z][A-Za-z'’.-]*[a-z][A-Za-z'’.-]*|[A-Z][a-z]*['’][A-Z][a-z]+", word):
        return False
    return word.casefold().strip(".") not in _NOT_NAME


def as_name(text: str) -> str | None:
    """``text`` as a person's name (two to five words, each capitalised), or None."""
    text = text.strip(" .,;:")
    words = text.split()
    if not 2 <= len(words) <= 5:
        return None
    if words[0].casefold() in _PARTICLES or not all(_word_ok(w) for w in words):
        return None
    return text


def split_name(name: str) -> tuple[str, str]:
    """First and last name. A middle initial and a suffix are dropped; two surnames stay together
    ("Saul Tapia Vega" is Saul, Tapia Vega), since the city printed both."""
    words = [w for w in name.split() if not re.fullmatch(r"[A-Z]\.", w)]
    while len(words) > 2 and words[-1].casefold().rstrip(",") in _SUFFIXES:
        words.pop()
    return words[0], " ".join(words[1:])


def _role_text(role: str, seat: str = "") -> str:
    role = " ".join(role.split())
    if re.fullmatch(r"mayor\s*pro[\s-]*tem(?:pore)?", role, re.IGNORECASE):
        role = "Mayor Pro Tem"
    elif role.casefold() == "mayor":
        role = "Mayor"
    elif role.casefold() in ("councilmember", "council member"):
        role = "Council Member"
    return f"{role}, {seat}" if seat else role


def _clean(line: str) -> str:
    return _CONTACT.sub("", line).strip(" ,;:|")


def _member_on(line: str, following: str) -> tuple[str, str] | None:
    """(name, role) when ``line`` (with the line after it) prints a member, else None."""
    line = _clean(line)
    if not line or len(line) > 90:
        return None
    match = _NAME_THEN_TITLE.match(line)
    if match:
        name = as_name(match["name"])
        if name:
            return name, _role_text(match["role"])
    match = _TITLE_THEN_NAME.match(line)
    if match:
        rest = match["name"]
        seat_match = _SEAT_TAIL.search(rest)
        seat = seat_match["seat"] if seat_match else ""
        if seat_match:
            rest = rest[: seat_match.start()]
        name = as_name(rest)
        if name:
            return name, _role_text(match["role"], seat)
    name = as_name(line)
    if name:
        match = _ROLE_FIRST.match(_clean(following))
        if match:
            return name, _role_text(match["role"])
    return None


def _member_title(body: str, roles: list[str]) -> str:
    text = (body + " " + " ".join(roles)).casefold()
    if "trustee" in text:
        return "Trustee"
    if "commissioner" in text:
        return "Commissioner"
    return "Council Member"


def read_members(text: str, body: str = "") -> tuple[list[Member], list[str]]:
    """The members a page prints, and the names among them printed with no title."""
    lines = page_lines(text)
    hits: dict[int, tuple[str, str]] = {}
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else ""
        found = _member_on(line, following)
        if found:
            hits[index] = found
    if not hits:
        return [], []
    # A name alone, right after a titled member (contact lines between), is a member too.
    untitled: dict[int, str] = {}
    for start in sorted(hits):
        index = start + 1
        while index < len(lines) and index not in hits:
            line = _clean(lines[index])
            if line:
                name = as_name(line)
                if name is None:
                    break
                untitled[index] = name
            index += 1
    title = _member_title(body, [role for _, role in hits.values()])
    rank = {"mayor": 0, "mayor pro tem": 1}
    chosen: dict[str, tuple[int, str]] = {}  # name -> (order, role)
    for index in sorted(set(hits) | set(untitled)):
        name, role = hits[index] if index in hits else (untitled[index], title)
        if name in chosen:
            old = chosen[name][1]
            if rank.get(role.casefold(), 9) < rank.get(old.casefold(), 9):
                chosen[name] = (chosen[name][0], role)
            continue
        chosen[name] = (index, role)
    members = []
    for name, (_, role) in sorted(chosen.items(), key=lambda kv: kv[1][0]):
        first_name, last_name = split_name(name)
        members.append(Member(name=name, first=first_name, last=last_name, role=role))
    printed = {name for name, _ in hits.values()}
    return members, [name for name in untitled.values() if name not in printed]


def _same_site(url: str, home: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    base = (urlsplit(home).hostname or "").lower().removeprefix("www.")
    return bool(host) and (host == base or host.endswith("." + base))


def _lead(text: str, body: str) -> int:
    if not text or len(text) > 60 or _AWAY.search(text):
        return 0
    score = max((weight for pattern, weight in _LEADS if pattern.search(text)), default=0)
    if body and body.casefold() in text.casefold():
        score += 2
    return score


def _heads_body(members: list[Member]) -> bool:
    return any(
        m.role.casefold().startswith(("mayor", "county commissioner", "commissioner"))
        for m in members
    )


def find_members(
    website: str,
    body: str,
    fetcher: Fetcher,
    *,
    open_in_browser: Callable[[str], Page] | None = None,
    pause: Callable[[], None] = lambda: time.sleep(PAUSE_S),
    wait: Callable[[float], None] = time.sleep,
) -> Found:
    """Follow ``website``'s links to the members page and read the members from it.

    A page that turns the plain client away (HTTP 403) is opened with ``open_in_browser`` when it
    is given. A page that answers HTTP 429 (too many requests) is asked again after each wait in
    :data:`RETRY_S`.
    """
    found = Found(body=body)
    if not website:
        found.problem = "no website to look for the members on"
        return found
    blocked = False

    def get(url: str) -> Page | None:
        nonlocal blocked
        if found.asked:
            pause()
        found.asked.append(url)
        waits = list(RETRY_S)
        while True:
            try:
                return fetcher.get(url)
            except FetchError as exc:
                problem = str(exc)
            if "HTTP 429" not in problem or not waits:
                break
            wait(waits.pop(0))
        if open_in_browser is not None and "HTTP 403" in problem:
            blocked = True
            try:
                return open_in_browser(url)
            except FetchError:
                return None
        return None

    queue: list[tuple[int, int, str]] = [(99, 0, website)]  # (-lead, depth, url)
    seen = {website.split("#", 1)[0].rstrip("/")}
    best: tuple[tuple[int, int], list[Member], list[str], str] | None = None
    rosters: list[tuple[tuple[int, int], list[Member], str]] = []
    read = 0
    while queue and read < MAX_PAGES:
        queue.sort(key=lambda item: (-item[0], item[1]))
        _, depth, url = queue.pop(0)
        page = get(url)
        read += 1
        if page is None:
            continue
        text = page.text
        members, untitled = read_members(text, body)
        if len(members) >= MIN_MEMBERS:
            score = (int(_heads_body(members)), len(members))
            rosters.append((score, members, page.url))
            if best is None or score > best[0]:
                best = (score, members, untitled, page.url)
        if depth >= 2:
            continue
        _, links, _ = parse(text, page.url)
        for link in links:
            target = link.url.split("#", 1)[0]
            if not target.startswith("http") or not _same_site(target, website):
                continue
            lead = _lead(link.text, body)
            key = target.rstrip("/")
            if lead and key not in seen:
                seen.add(key)
                queue.append((lead, depth + 1, target))
    if best is None:
        found.problem = (
            f"no page on {website} lists the {body or 'council'} members with their titles"
            + (" (the site opened only in a browser)" if blocked else "")
        )
        return found
    score, found.members, found.untitled, found.page = best
    names = {m.name for m in found.members}
    for other_score, other, url in rosters:
        extra = [m.name for m in other if m.name not in names]
        if other_score == score and url != found.page and extra:
            found.differ.append((url, extra))
    if not body:
        found.body = _body_from(found.members)
    return found


def _body_from(members: list[Member]) -> str:
    roles = " ".join(m.role for m in members).casefold()
    if "trustee" in roles:
        return "Board of Trustees"
    if "commissioner" in roles:
        return "Board of County Commissioners"
    return "City Council"


def roster_from_found(name: str, state: str, found: Found, today: date) -> dict[str, Any]:
    """A ``roster.json`` from what :func:`find_members` read."""
    note = (
        "Names as the city's page prints them, read by rule (no model) on "
        f"{today.isoformat()} from the page in sources."
    )
    if found.untitled:
        title = _member_title(found.body, [m.role for m in found.members])
        note += (
            f" The page prints no title for {', '.join(found.untitled)}; "
            f"they are listed as {title}."
        )
    for page, extra in found.differ:
        note += (
            f" {page} also lists {found.body or 'the'} members, with {', '.join(extra)} "
            "in place of names on this page; check which page is current."
        )
    return {
        "city": name,
        "state": state,
        "body": found.body,
        "retrieved": today.isoformat(),
        "sources": [found.page],
        "note": note,
        "members": [m.to_json() for m in found.members],
    }
