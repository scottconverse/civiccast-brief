"""Weekly roundup: several meeting recaps combined into one recap for one script.

Claim ids get a meeting prefix ("m1-c03") so they stay unique, and each claim's text starts with
its meeting date in brackets, so the script can say which meeting a fact is from. The date comes
from the recap header (the video title), never from the model. The header's date line lists every
meeting date, so the intro may name them without citing a claim.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime

from civiccast_brief.script.recap import Recap
from civiccast_brief.script.writer import ROUNDUP_ID


class RoundupError(ValueError):
    """The recaps cannot be combined."""


def _date_key(recap: Recap) -> datetime:
    """Sort key: the header date ("October 6, 2026"). A date that does not parse sorts last."""
    try:
        return datetime.strptime(recap.meeting_date.strip(), "%B %d, %Y")
    except ValueError:
        return datetime.max


def _body_label(
    recaps: list[Recap],
    site: str,
    bodies: list[str],
    places: Mapping[tuple[str, str], str],
) -> str:
    """Each body named with the place it serves, when a body is not one of the site's own:
    "Boulder Valley Board of Education and Louisville City Council". Empty when every body is
    the site's own city's, so such a roundup reads exactly as it did before. A carried body
    whose place is not known keeps its name with no place: the site's city is never attached
    to another city's board."""
    if not site or all(r.city.casefold() == site.casefold() for r in recaps):
        return ""
    first = {r.body: r for r in recaps}
    named = []
    for body in bodies:
        recap = first[body]
        if recap.city.casefold() == site.casefold():
            place = site
        else:
            place = places.get((recap.city.casefold(), body.casefold()), "")
        name = " ".join(body.split())
        if place and not name.casefold().startswith(f"{place.casefold()} "):
            name = f"{place} {name}"
        named.append(name)
    return " and ".join(named)


def combine(
    recaps: list[Recap],
    *,
    city: str = "",
    shared: Iterable[tuple[str, str]] = (),
    places: Mapping[tuple[str, str], str] | None = None,
) -> Recap:
    """One roundup of ``recaps``. ``city`` is the site's city and ``shared`` the (city, body)
    of each board that city's site carries from another city (a school district's): those
    recaps are the site's own, and the roundup is the site's city's. Without ``city`` every
    recap must name the same city, as before. ``places`` maps (city, body) to the place that
    body's channel names, for naming a carried board in the roundup's labels."""
    if len(recaps) < 2:
        raise RoundupError("a roundup needs at least two meeting recaps")
    cities = {r.city for r in recaps}
    if city:
        carried = {(c.casefold(), b.casefold()) for c, b in shared}
        stray = {
            r.city
            for r in recaps
            if r.city.casefold() != city.casefold()
            and (r.city.casefold(), r.body.casefold()) not in carried
        }
        if stray:
            raise RoundupError(f"a roundup covers one city, got {sorted(cities)}")
    elif len(cities) != 1:
        raise RoundupError(f"a roundup covers one city, got {sorted(cities)}")
    recaps = sorted(recaps, key=_date_key)  # oldest meeting first; stable for equal dates
    bodies = sorted({r.body for r in recaps})
    claims = []
    for number, recap in enumerate(recaps, start=1):
        for claim in recap.claims:
            claims.append(
                replace(
                    claim,
                    id=f"m{number}-{claim.id}",
                    topic=f"{recap.meeting_date}: {claim.topic}",
                    text=f"[{recap.meeting_date} meeting] {claim.text}",
                )
            )
    site = city or recaps[0].city
    return Recap(
        city=site,
        body=" and ".join(bodies),
        meeting_date=" and ".join(dict.fromkeys(r.meeting_date for r in recaps)),
        video_id=ROUNDUP_ID,
        claims=tuple(claims),
        video_place=recaps[0].video_place,
        body_label=_body_label(recaps, site, bodies, places or {}),
    )
