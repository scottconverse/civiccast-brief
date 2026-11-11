"""Meeting lists from CivicClerk, eScribe and CivicPlus, read with rules and no model.

Each platform is asked the way its own public pages ask it (checked live on 2026-11-10 against
Erie, Boulder County, Lyons and Lafayette):

- CivicClerk: ``https://<tenant>.api.civicclerk.com/v1`` answers JSON. ``EventCategories`` names
  the bodies and ``Events`` lists meetings, newest first, with their published agenda files.
- eScribe: ``https://pub-<tenant>.escribemeetings.com`` lists meetings when its calendar is POSTed
  a date range, and shows each agenda as an HTML page of numbered items.
- CivicPlus: the town's own site has an Agenda Center (``/AgendaCenter``, one panel per body) and
  an Archive Center (``/Archive.aspx``, one list per body and kind of document).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from civiccast_brief.citybuild.fetch import Fetcher
from civiccast_brief.citybuild.page import first_date, squash

#: How many CivicClerk meetings one request lists, newest first.
CLERK_EVENTS: Final = 200
#: How far back the eScribe calendar is asked for meetings.
ESCRIBE_DAYS: Final = 120
ESCRIBE_CALENDAR: Final = "/MeetingsCalendarView.aspx/GetCalendarMeetings"
_TAG: Final = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Doc:
    """One meeting document a platform lists."""

    body: str
    day: date
    title: str
    url: str
    #: "agenda", "packet" or "minutes".
    what: str


def _text(fragment: str) -> str:
    return squash(html.unescape(_TAG.sub(" ", fragment)))


# ----- CivicClerk -------------------------------------------------------------------------------


def clerk_api(tenant: str) -> str:
    return f"https://{tenant}.api.civicclerk.com/v1"


def clerk_bodies(tenant: str, fetcher: Fetcher) -> list[str]:
    """The tenant's event categories, which are its bodies, less the catch-all "General"."""
    data = fetcher.get(f"{clerk_api(tenant)}/EventCategories").json()
    rows = data.get("value") if isinstance(data, dict) else None
    names = [
        squash(str(row.get("categoryDesc")))
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and row.get("categoryDesc")
    ]
    return [n for n in dict.fromkeys(names) if n.casefold() != "general"]


def clerk_events_url(tenant: str, today: date) -> str:
    before = (today + timedelta(days=1)).isoformat()
    return (
        f"{clerk_api(tenant)}/Events?$orderby=startDateTime%20desc&$top={CLERK_EVENTS}"
        f"&$filter=startDateTime%20lt%20{before}T00:00:00Z"
    )


def clerk_file_url(tenant: str, file_id: int) -> str:
    return f"{clerk_api(tenant)}/Meetings/GetMeetingFileStream(fileId={file_id},plainText=false)"


def clerk_docs(tenant: str, fetcher: Fetcher, today: date) -> list[Doc]:
    """Agenda and packet files of meetings on or before ``today``, newest meeting first.

    The API writes the town's local time with a Z ("2026-11-10T19:30:00Z" for a 7:30 PM
    meeting), so the day is read as written.
    """
    data = fetcher.get(clerk_events_url(tenant, today)).json()
    rows = data.get("value") if isinstance(data, dict) else None
    found: list[Doc] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = squash(str(row.get("eventName") or ""))
        try:
            day = datetime.fromisoformat(str(row.get("startDateTime"))[:19]).date()
        except ValueError:
            continue
        if day > today or "cancel" in title.casefold():
            continue
        body = squash(str(row.get("categoryName") or ""))
        files = [f for f in row.get("publishedFiles") or [] if isinstance(f, dict)]
        for wanted, what in (("agenda", "agenda"), ("agenda packet", "packet")):
            for file in files:
                if str(file.get("type") or "").casefold() == wanted and file.get("fileId"):
                    url = clerk_file_url(tenant, int(file["fileId"]))
                    found.append(Doc(body, day, f"{title} ({body})", url, what))
    return found


# ----- eScribe ----------------------------------------------------------------------------------

_MEETING_SUFFIX: Final = re.compile(
    r"\s+-\s+(?:regular|special|study|joint|work)?\s*(?:meeting|session|hearing)\s*$",
    re.IGNORECASE,
)
_ESCRIBE_ITEM: Final = re.compile(
    r"class='AgendaItemCounter'[^>]*>([^<]*)</DIV>.*?class='AgendaItemTitle'[^>]*>(.*?)</DIV>",
    re.IGNORECASE | re.DOTALL,
)
_TITLE: Final = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def escribe_base(tenant: str) -> str:
    return f"https://pub-{tenant}.escribemeetings.com"


def escribe_body(meeting_type: str) -> str:
    """ "Board of County Commissioners - Regular Meeting" is the Board of County Commissioners."""
    return _MEETING_SUFFIX.sub("", squash(meeting_type))


def escribe_meetings(tenant: str, fetcher: Fetcher, today: date) -> list[dict[str, Any]]:
    """The calendar's meetings from :data:`ESCRIBE_DAYS` ago to today, newest first."""
    span = {
        "calendarStartDate": (today - timedelta(days=ESCRIBE_DAYS)).isoformat(),
        "calendarEndDate": (today + timedelta(days=1)).isoformat(),
    }
    data = fetcher.post_json(escribe_base(tenant) + ESCRIBE_CALENDAR, span).json()
    rows = data.get("d") if isinstance(data, dict) else None
    meetings = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            day = datetime.strptime(str(row.get("StartDate"))[:10], "%Y/%m/%d").date()
        except ValueError:
            continue
        if day <= today:
            meetings.append({**row, "day": day})
    meetings.sort(key=lambda m: str(m.get("StartDate")), reverse=True)
    return meetings


def escribe_bodies(meetings: list[dict[str, Any]]) -> list[str]:
    names = [
        escribe_body(str(m.get("MeetingType") or m.get("MeetingName") or "")) for m in meetings
    ]
    return [n for n in dict.fromkeys(names) if n]


def escribe_html_links(meeting: dict[str, Any]) -> list[tuple[str, str]]:
    """The meeting's HTML agenda and minutes pages, agenda first, as (what, path)."""
    links = [d for d in meeting.get("MeetingDocumentLink") or [] if isinstance(d, dict)]
    found = []
    for wanted, what in (("agenda", "agenda"), ("postminutes", "minutes")):
        for link in links:
            if (
                str(link.get("Type") or "").casefold() == wanted
                and str(link.get("Format") or "").upper() == "HTML"
                and link.get("Url")
            ):
                found.append((what, str(link["Url"])))
    return found


def escribe_items(text: str) -> tuple[str, list[str]]:
    """The page title and its numbered items ("1. Call to Order")."""
    title = _TITLE.search(text)
    items = [
        f"{squash(number)} {_text(name)}".strip()
        for number, name in _ESCRIBE_ITEM.findall(text)
        if _text(name)
    ]
    return (_text(title.group(1)) if title else ""), items


# ----- CivicPlus --------------------------------------------------------------------------------

_CATEGORY: Final = re.compile(
    r'aria-controls="category-panel-(\d+)"[^>]*>\s*(.*?)\s*</h2>', re.IGNORECASE | re.DOTALL
)
_AGENDA_FILE: Final = re.compile(
    r'href="(/AgendaCenter/ViewFile/(Agenda|Minutes)/_(\d{2})(\d{2})(\d{4})-(\d+))"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_MODULE: Final = re.compile(
    r'<label for="amidDDN(\d+)">(.*?)</label>.*?<select[^>]*id="amidDDN\1"[^>]*>(.*?)</select>',
    re.IGNORECASE | re.DOTALL,
)
_OPTION: Final = re.compile(r'<option value="\d+_\d+_0_(\d+)"[^>]*>(.*?)</option>', re.IGNORECASE)
_MODULE_KIND: Final = re.compile(r"\s+(agendas?|minutes|resolutions?)\s*$", re.IGNORECASE)


def agenda_center(host: str, text: str) -> list[Doc]:
    """Every dated agenda and minutes file in an Agenda Center page, by its body's panel."""
    names = {number: _text(name) for number, name in _CATEGORY.findall(text)}
    found: list[Doc] = []
    for number, body in names.items():
        start = text.find(f'id="category-panel-{number}"')
        if start < 0:
            continue
        end = text.find('id="category-panel-', start + 1)
        panel = text[start : end if end > 0 else len(text)]
        seen: set[str] = set()
        for path, kind, month, day, year, _id, label in _AGENDA_FILE.findall(panel):
            if path in seen:
                continue
            seen.add(path)
            try:
                when = date(int(year), int(month), int(day))
            except ValueError:
                continue
            what = "minutes" if kind.casefold() == "minutes" else "agenda"
            title = _text(label)
            if title.casefold() in ("", "agenda", "minutes", "html", "pdf"):
                title = f"{body} {what}"
            found.append(Doc(body, when, title, f"https://{host}{path}", what))
    return found


def agenda_center_bodies(text: str) -> list[str]:
    return list(dict.fromkeys(_text(name) for _, name in _CATEGORY.findall(text) if _text(name)))


def _module_body(label: str) -> tuple[str, str]:
    name = squash(html.unescape(label)).rstrip(":").strip()
    kind = _MODULE_KIND.search(name)
    what = (kind.group(1).casefold() if kind else "").rstrip("s")
    body = _MODULE_KIND.sub("", name).strip()
    return body, {"agenda": "agenda", "minute": "minutes", "resolution": "resolution"}.get(what, "")


def archive_center(host: str, text: str) -> list[Doc]:
    """Every dated item in an Archive Center page's agenda and minutes lists.

    An item's date is the first date written in its name; an item with none ("Nobiembre 02 del
    2026", the Spanish copy of an agenda) is left out.
    """
    found: list[Doc] = []
    for _module, label, options in _MODULE.findall(text):
        body, what = _module_body(label)
        if what == "resolution":
            continue
        for item, name in _OPTION.findall(options):
            title = _text(name)
            when = first_date(title)
            if when is None:
                continue
            url = f"https://{host}/ArchiveCenter/ViewFile/Item/{item}"
            found.append(Doc(body, when, title, url, what or "agenda"))
    return found


#: An Archive Center list with no "Agendas" or "Minutes" in its name is a body's only when its name
#: names a body ("Local Liquor Licensing Authority") and not some other kind of document.
_BODY_WORD: Final = re.compile(
    r"\b(?:board|commission|committee|subcommittee|council|authority|trustees|task ?force|"
    r"working group|district)\b",
    re.IGNORECASE,
)
_NOT_MEETINGS: Final = re.compile(
    r"\b(?:notices?|reports?|budgets?|ordinances?|financials?|newsletters?|messages?|profile|"
    r"applications?|articles?)\b",
    re.IGNORECASE,
)


def archive_center_bodies(text: str) -> list[str]:
    """The bodies with an agenda list, or with an unnamed list whose name is a body's.

    Erie's "Mayor's Message" and Lyons's "Board of Trustees Public Hearing Notices" are lists of
    other documents, not of meetings, and are left out.
    """
    names = []
    for _module, label, _options in _MODULE.findall(text):
        body, what = _module_body(label)
        if not body:
            continue
        if what == "agenda" or (
            what == "" and _BODY_WORD.search(body) and not _NOT_MEETINGS.search(body)
        ):
            names.append(body)
    return list(dict.fromkeys(names))
