"""Short titles and labels for roundups.

A weekly roundup covers several meetings. Its dated title is a short range ("Longmont weekly
roundup, Sept. 22 – Nov. 3"), never every date strung together. Roundups built before that rule
carry the long form in their JSON; :func:`shorten_roundup_title` turns it into the short one so
the site does not show it. A roundup's recap lines start with a bracketed label ("[September 22,
2026 meeting] ..."); :func:`split_meeting_label` takes that label off the sentence.
"""

from __future__ import annotations

import re
from datetime import date, datetime

MONTHS_SHORT = (
    "Jan.",
    "Feb.",
    "March",
    "April",
    "May",
    "June",
    "July",
    "Aug.",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
)
_LONG_DATE = r"[A-Z][a-z]+ \d{1,2}, \d{4}"
_LONG_TITLE = re.compile(
    rf"^(?P<city>.+?) weekly roundup: (?P<dates>{_LONG_DATE}(?: and {_LONG_DATE})+)$"
)
_LABEL = re.compile(r"^\[(?P<label>[^\]]+?) meeting\]\s*")


def _short(day: date, *, year: bool) -> str:
    text = f"{MONTHS_SHORT[day.month - 1]} {day.day}"
    return f"{text}, {day.year}" if year else text


def roundup_title(city: str, days: list[date]) -> str:
    """ "Longmont weekly roundup, Sept. 22 – Nov. 3". The year is added when the dates span two."""
    first, last = min(days), max(days)
    if first == last:
        span = _short(last, year=False)
    else:
        years = first.year != last.year
        span = f"{_short(first, year=years)} – {_short(last, year=years)}"
    return f"{city} weekly roundup, {span}"


def shorten_roundup_title(headline: str) -> str:
    """The short title for a headline made as "City weekly roundup: A and B and C", else as is."""
    match = _LONG_TITLE.match(headline)
    if match is None:
        return headline
    try:
        days = [
            datetime.strptime(part.strip(), "%B %d, %Y").date()
            for part in match.group("dates").split(" and ")
        ]
    except ValueError:
        return headline
    return roundup_title(match.group("city"), days)


def split_meeting_label(text: str) -> tuple[str | None, str]:
    """("September 22, 2026", "The sentence.") from "[September 22, 2026 meeting] The sentence."."""
    match = _LABEL.match(text)
    if match is None:
        return None, text
    return match.group("label"), text[match.end() :]
