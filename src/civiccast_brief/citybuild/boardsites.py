"""Board rosters read from the town's own board pages, for ``rosters/<body>.json``.

A town's site has a page for each board with its members. The towns lay the list out in a handful
of ways, all seen on 2026-11-11:

* a "Members" heading, then a table of Name, Email, Expires (Erie), Name, Title, Term Ending Date
  (Lafayette), or one name per line with the title after a comma (Lyons);
* the same with the title on the next line (Boulder: "Commission Member", "Term Years");
* "2026 Planning Commission Members" and plain names under it (Louisville);
* "Name, Chair (term ends Feb. 28, 2028)" under "Current Members" (Boulder County).

The members are read by rule; no model is asked. Only names and titles are kept, never the email
address or the phone number beside them, and staff and council liaisons are left out. A board
whose page prints no member list is a gap and is reported with the pages checked.
"""

from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from civiccast_brief.citybuild.boards import ROSTERS_DIR, _empty, _governing, slug
from civiccast_brief.citybuild.fetch import Fetcher, FetchError, Page
from civiccast_brief.citybuild.members import _ANY, _UP, as_name, page_lines, split_name
from civiccast_brief.citybuild.page import parse
from civiccast_brief.sources import Source

#: Fewer members than this is not a roster.
MIN_MEMBERS: Final = 3
#: Index pages read, and board pages tried for one body.
INDEX_PAGES: Final = 4
PAGES_PER_BODY: Final = 3
#: Seconds between requests to one town's site.
PAUSE_S: Final = 1.0
SECTION_LINES: Final = 80

_HEADING = re.compile(
    r"^(?:[\w&'’.,/ -]{0,60}\s)?(?:Members?|Membership)(?:\s*(?:&|and)\s*Liaisons)?:?$",
    re.IGNORECASE,
)
_ROLE_WORDS = (
    r"chair(?:person|man|woman)?|member|commissioner|trustee|alternate|representative|president"
    r"|secretary|treasurer|ex[- ]officio"
)
#: A title on its own line, with a trailing "(Term ends June 2030)" allowed. Boulder prints the
#: board's name before the title ("Beverage Licensing Authority Board Member / Vice Chair").
_ROLE_LINE = re.compile(
    rf"^(?:[\w'’./-]+\s*){{0,8}}(?:vice[- ]?)?(?:{_ROLE_WORDS})\b[^()]*?(?:\s*\(.*\))?$",
    re.IGNORECASE,
)
_ROLE_PHRASE = re.compile(
    rf"(?:(?:second|assistant|1st|2nd)\s+)?(?:vice[- ]?)?(?:{_ROLE_WORDS})(?:\s+member)?",
    re.IGNORECASE,
)
#: A heading that starts the staff (or liaisons) after the members.
_END = re.compile(r"^(?:staff(?:\s+(?:participants|members))?|liaisons)$", re.IGNORECASE)
#: Page furniture that looks like a name ("Quick Links" under a board's list).
_NOT_NAME_HERE = re.compile(
    r"\b(?:quick|links?|resources?|related|documents?|follow|subscribe|search|share|print|site"
    r"|privacy|policy|accessibility|menu|footer|translate|login|apply|open|vacant)\b",
    re.IGNORECASE,
)
#: A title that is one word and says what kind of seat it is (Boulder's "Resident").
_SEAT_KINDS = frozenset({"resident", "student", "youth", "citizen", "owner", "designee"})
_STAFF = re.compile(
    r"\b(?:liaison|staff|planner|director|manager|coordinator|specialist|engineer|attorney|counsel"
    r"|administrator|analyst|technician|supervisor|clerk|advis[eo]r|volunteer|curator)\b",
    re.IGNORECASE,
)
#: Pronouns and notes printed after a name ("Mindy Miller (she, her, ella)", "*habla español").
_NAME_NOTE = re.compile(
    r"\s*(?:\((?=[^)]*\b(?:he|she|they|them|her|him|ella|él|el|ze)\b)[^)]*\)|\*.*)\s*$",
    re.IGNORECASE,
)
_TERM = re.compile(
    r"^(?:term\b.*|expires?\b.*|exp\.?\b.*|n/?a|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}\s*[-–]\s*\d{4}"
    r"|\d{1,2}/\d{4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?\d{4})$",
    re.IGNORECASE,
)
_TERM_TAIL = re.compile(r"\s*\((?:term|expires?|exp\.?)\b[^)]*\)\s*$", re.IGNORECASE)
_HEADER = frozenset(
    {"name", "email", "title", "term", "expires", "contact", "phone", "position", "role", "seat"}
    | {"term ending date", "term years", "term expires", "title / term expires", "e-mail"}
)
_CONTACT = re.compile(
    r"@|\(\d{3}\)|\d{3}[-.\s]\d{3}[-.\s]\d{4}|^(?:office|cell|phone|email)\b", re.IGNORECASE
)
_NICK = re.compile(rf"^[(“\"‘']{_UP}{_ANY}*[)”\"’']$")


def _board_name(text: str) -> str | None:
    """``text`` as a person's name; a nickname in quotes or parentheses between the first and
    last name is part of it ("Jean “Raini” Ott", "Robert (Bob) Braudes")."""
    text = _NAME_NOTE.sub("", text).strip(" .,;:")
    if _STAFF.search(text) or _NOT_NAME_HERE.search(text):
        return None
    words = text.split()
    plain = [w for i, w in enumerate(words) if not (0 < i < len(words) - 1 and _NICK.match(w))]
    if as_name(" ".join(plain)) is None:
        return None
    return text


def _first_last(name: str) -> tuple[str, str]:
    words = name.split()
    plain = [w for i, w in enumerate(words) if not (0 < i < len(words) - 1 and _NICK.match(w))]
    return split_name(" ".join(plain))


def _title(role: str) -> str:
    """The title alone: "Vice Chair" from "Beverage Licensing Authority Board Member / Vice Chair"
    or "Vice Chair, Resident"."""
    role = _TERM_TAIL.sub("", role).strip(" ,;:-–—")
    parts = [p for p in re.split(r"\s*[/,]\s*", role) if _ROLE_PHRASE.search(p)]
    parts.sort(key=lambda p: not re.search(r"chair", p, re.IGNORECASE))
    if not parts:
        return " ".join(role.split()) or "Member"
    match = _ROLE_PHRASE.search(parts[0])
    return " ".join(match[0].split()).title() if match else "Member"


def _line_member(line: str) -> tuple[str, str] | None | bool:
    """(name, role) when the line holds a name and its title, a bare name as (name, ""), False when
    it names someone to leave out (staff, a liaison), else None."""
    if _CONTACT.search(line):
        return None
    text = _TERM_TAIL.sub("", line).strip()
    while (stripped := _NAME_NOTE.sub("", text)) != text:
        text = stripped
    head, role = text, ""
    match = re.match(r"^(.+?)\s*(?:,|\s[—–-])\s*(.+)$", text)
    if match:
        head, role = match[1], match[2].strip()
    name = _board_name(head)
    if name is None or _ROLE_LINE.match(name):  # "Vice Chair, Resident" is a title
        return None
    if not role:
        return name, ""
    if _STAFF.search(role):
        return False
    if re.search(rf"(?:\b|-)(?:{_ROLE_WORDS})\b", role, re.IGNORECASE):
        return name, role
    if len(role.split()) <= 4 and not re.search(r"\d", role):
        return name, role  # "Reagan Friedman, Recorder"
    return None


def read_board_members(text: str) -> list[dict[str, str]]:
    """The members on a board's page: the longest list under a "Members" heading, or under a
    table's header row of "Name" and "Title" (Nederland's table has no heading)."""
    lines = page_lines(text)
    best: list[dict[str, str]] = []
    for start, line in enumerate(lines):
        table = line.casefold() in ("name", "member name") and start + 1 < len(lines)
        table = table and lines[start + 1].casefold() in _HEADER
        if not table and (len(line) > 60 or not _HEADING.match(line)):
            continue
        found = _section(lines[start + 1 : start + 1 + SECTION_LINES])
        if len(found) > len(best):
            best = found
    return best if len(best) >= MIN_MEMBERS else []


def _person(line: str) -> bool:
    """Whether the line is itself a person (a member, or staff to leave out) and not a title."""
    hit = _line_member(line)
    if hit is None:
        return False
    return hit is False or bool(hit[1]) or not _ROLE_LINE.match(line)


def _section(lines: list[str]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    skip_next = False
    for index, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        if _END.match(line):
            break
        low = line.casefold().rstrip(":")
        if (
            low in _HEADER
            or _TERM.match(line)
            or _CONTACT.search(line)
            or low.startswith("vacan")
            or low == "open"
        ):
            continue
        hit = _line_member(line)
        if hit is False:
            continue
        if hit is None:
            if _ROLE_LINE.match(line) or _STAFF.search(line):
                continue  # a title or a job title the name above it has already taken
            if found:
                break  # past the list: a sentence, or the next heading
            continue
        name, role = hit
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if not role and following and not _TERM.match(following) and not _person(following):
            if (
                _STAFF.search(following)
                and not _END.match(following)
                and _line_member(following) is None
            ):
                continue  # "Community Planner" under a name: staff
            if _ROLE_LINE.match(following) and not _STAFF.search(following):
                role, skip_next = following, True
            elif following.casefold() in _SEAT_KINDS:
                role, skip_next = following, True  # "Resident"
            elif _ROLE_LINE.match(following):
                continue  # "Staff Liaison" under a name
        if name in seen:
            continue
        seen.add(name)
        first, last = _first_last(name)
        found.append({"name": name, "first": first, "last": last, "role": _title(role)})
    return found


# ----- Finding the board's page ----------------------------------------------------------------

#: Words of a link that leads to the town's list of boards ("Boards & Commissions").
_INDEX_WORDS = frozenset(
    [
        "advisory",
        "board",
        "boards",
        "commission",
        "commissions",
        "committee",
        "committees",
        "and",
        "city",
        "town",
        "county",
    ]
)
_GOVERNMENT = re.compile(r"^(?:city |town |county )?government$", re.IGNORECASE)
_NOT_A_BOARD_PAGE = re.compile(
    r"\bjoin\b|apply|application|meeting|agenda|minutes|vacanc|contact|video|packet|calendar"
    r"|become|about|recruit|volunteer|resident|guide|handbook|bylaw|stream|news",
    re.IGNORECASE,
)


def _words(text: str) -> list[str]:
    text = re.sub(r"\([^)]*\)", " ", text.replace("&", " and "))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).split()


def _acronym(word: str) -> bool:
    return word.isalpha() and word.isupper() and 2 <= len(word) <= 6


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\([^)]*\)", " ", text.replace("&", " and "))
    return [w for w in re.sub(r"[^A-Za-z0-9]+", " ", text).split() if w.casefold() != "and"]


def _same(body: list[str], link: list[str]) -> bool:
    """Whether the words of a body's name are the link's words; an acronym in the body (BJAD)
    stands for the link words with those initials ("Boulder Junction Access District")."""
    if not body or not link:
        return not body and not link
    head = body[0]
    if _acronym(head):
        n = len(head)
        if "".join(w[0] for w in link[:n]).upper() == head and _same(body[1:], link[n:]):
            return True
    return head.casefold() == link[0].casefold() and _same(body[1:], link[1:])


def _score(body: str, text: str) -> int:
    """How well a link's text names the body: 3 the same, 2 one holds the other, 0 no."""
    if _NOT_A_BOARD_PAGE.search(text):
        return 0
    link = _words(text)
    if not link:
        return 0
    parts = [body, *[p for p in re.split(r"\s*/\s*", body) if p != body]]
    for part in parts:
        if _words(part) == link or _same(_tokens(part), _tokens(text)):
            return 3
    for part in parts:
        words = _words(part)
        if len(words) >= 2 and (_contains(link, words) or _contains(words, link)):
            return 2
    return 0


def _contains(big: list[str], small: list[str]) -> bool:
    n = len(small)
    return len(small) >= 2 and any(big[i : i + n] == small for i in range(len(big) - n + 1))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


class _Reader:
    def __init__(
        self,
        fetcher: Fetcher,
        open_in_browser: Callable[[str], Page] | None,
        pause: Callable[[], None],
    ) -> None:
        self.fetcher, self.open_in_browser, self.pause = fetcher, open_in_browser, pause
        self.asked: list[str] = []

    def get(self, url: str) -> Page | None:
        if self.asked:
            self.pause()
        self.asked.append(url)
        try:
            return self.fetcher.get(url)
        except FetchError as exc:
            if self.open_in_browser is not None and "HTTP 403" in str(exc):
                try:
                    return self.open_in_browser(url)
                except FetchError:
                    return None
            return None


def _links(page: Page, host: str) -> list[tuple[str, str]]:
    _, links, _ = parse(page.text, page.url)
    return [(l.text, l.url.split("#", 1)[0]) for l in links if _host(l.url) == host and l.text]


def _is_index(text: str) -> bool:
    words = _words(text)
    return bool(words) and set(words) <= _INDEX_WORDS and words != ["advisory"]


def board_pages(website: str, reader: _Reader) -> tuple[list[tuple[str, str]], list[str]]:
    """The links on the town's home page, site map and board index pages (text, address), and the
    index pages' addresses."""
    host = _host(website)
    pool: list[tuple[str, str]] = []
    indexes: list[str] = []
    root = f"{urlsplit(website).scheme}://{urlsplit(website).netloc}"
    for start in (website, root + "/sitemap"):
        page = reader.get(start)
        if page is not None:
            pool += _links(page, host)

    def find_indexes() -> None:
        for text, url in list(pool):
            if _is_index(text) and url not in indexes:
                indexes.append(url)

    find_indexes()
    if not indexes:  # Boulder's home page links "Government", which links the boards
        for url in list(dict.fromkeys(u for t, u in pool if _GOVERNMENT.match(t)))[:2]:
            page = reader.get(url)
            if page is not None:
                pool += _links(page, host)
        find_indexes()
    for url in indexes[:INDEX_PAGES]:
        page = reader.get(url)
        if page is not None:
            pool += _links(page, host)
    return pool, indexes[:INDEX_PAGES]


def _candidates(
    body: str, pool: list[tuple[str, str]], indexes: list[str]
) -> list[tuple[str, bool]]:
    """Addresses to try for the body, best first, each with whether it is only a guess."""
    scored: dict[str, int] = {}
    for text, url in pool:
        score = _score(body, text)
        if score and scored.get(url, 0) < score:
            scored[url] = score
    ranked = [(url, False) for url in sorted(scored, key=lambda u: -scored[u])]
    for index in indexes:  # a county's pages sit under its index, named like the board
        guess = index.rstrip("/") + "/" + slug(body) + "/"
        if "board" in index.casefold() and all(guess != url for url, _ in ranked):
            ranked.append((guess, True))
    return ranked


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>|<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def _about(body: str, page: Page, guessed: bool, url: str) -> bool:
    """Whether the page is the body's own: its title or main heading names the body, and a page
    asked for by a guessed address is at that address (a site that answers any address with
    another page is not the board's)."""
    if guessed and page.url.rstrip("/").casefold() != url.rstrip("/").casefold():
        return False
    for match in _TITLE.finditer(page.text):
        heading = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(match[1] or match[2])).split())
        for part in re.split(r"\s[|–—-]\s", heading):
            if _score(body, part) >= 2:
                return True
    return False


def write_site_rosters(
    folder: Path,
    sources: list[Source],
    city: str,
    state: str,
    fetcher: Fetcher,
    today: date,
    *,
    website: str = "",
    open_in_browser: Callable[[str], Page] | None = None,
    pause: Callable[[], None] = lambda: time.sleep(PAUSE_S),
) -> list[str]:
    """Write ``rosters/<body>.json`` for each body with no roster whose page on the town's site
    lists its members. Returns a line for each body tried."""
    wanted: list[str] = []
    for source in sources:
        for body in source.bodies:
            path = folder / ROSTERS_DIR / f"{slug(body)}.json"
            if body not in wanted and not _governing(body) and _empty(path):
                wanted.append(body)
    if not wanted:
        return []
    if not website:
        return [f"{body}: the city has no website on file to look for it on" for body in wanted]
    reader = _Reader(fetcher, open_in_browser, pause)
    pool, indexes = board_pages(website, reader)
    lines: list[str] = []
    used: dict[str, str] = {}
    for body in wanted:
        tried: list[str] = []
        members: list[dict[str, str]] = []
        page_url = ""
        for url, guessed in _candidates(body, pool, indexes)[:PAGES_PER_BODY]:
            page = reader.get(url)
            tried.append(url)
            if page is None or not _about(body, page, guessed, url):
                continue
            members = read_board_members(page.text)
            if members and page.url in used:
                tried.append(f"{page.url} (already {used[page.url]}'s)")
                members = []
            if members:
                page_url = page.url
                used[page_url] = body
                break
        if not members:
            lines.append(f"{body}: no member list found; checked {', '.join(tried) or website}")
            continue
        roster = {
            "city": city,
            "state": state,
            "body": body,
            "retrieved": today.isoformat(),
            "sources": [page_url],
            "note": (
                "Names and titles as the board's page on the town's site prints them (staff and "
                f"liaisons left out), read by rule (no model) on {today.isoformat()}."
            ),
            "members": members,
        }
        path = folder / ROSTERS_DIR / f"{slug(body)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(roster, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        lines.append(f"{body}: wrote {path.name}, {len(members)} members from {page_url}")
    return lines
