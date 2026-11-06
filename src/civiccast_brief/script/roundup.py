"""Weekly roundup: several meeting recaps combined into one recap for one script.

Claim ids get a meeting prefix ("m1-c03") so they stay unique, and each claim's text starts with
its meeting date in brackets, so the script can say which meeting a fact is from. The date comes
from the recap header (the video title), never from the model. The header's date line lists every
meeting date, so the intro may name them without citing a claim.
"""

from __future__ import annotations

from dataclasses import replace

from civiccast_brief.script.recap import Recap
from civiccast_brief.script.writer import ROUNDUP_ID


class RoundupError(ValueError):
    """The recaps cannot be combined."""


def combine(recaps: list[Recap]) -> Recap:
    if len(recaps) < 2:
        raise RoundupError("a roundup needs at least two meeting recaps")
    cities = {r.city for r in recaps}
    if len(cities) != 1:
        raise RoundupError(f"a roundup covers one city, got {sorted(cities)}")
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
    return Recap(
        city=recaps[0].city,
        body=" and ".join(bodies),
        meeting_date=" and ".join(r.meeting_date for r in recaps),
        video_id=ROUNDUP_ID,
        claims=tuple(claims),
        video_place=recaps[0].video_place,
    )
