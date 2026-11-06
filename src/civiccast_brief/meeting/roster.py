"""The official council roster: the only source of speaker names.

The roster file lists each member's name as the city prints it, with the role from the city's own
page. Caption text is not a source of names: it mangles them ("Mayor Dgoing", "Council member
Calcoffer"), so a caption name counts only when it matches a roster name exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROSTERS_DIR = Path(__file__).parent / "rosters"
#: The roster of the first city, kept as a worked example and for the tests.
EXAMPLE_ROSTER = ROSTERS_DIR / "longmont.json"


@dataclass(frozen=True)
class Member:
    name: str
    first: str
    last: str
    role: str


@dataclass(frozen=True)
class Roster:
    city: str
    body: str
    state: str
    sources: tuple[str, ...]
    members: tuple[Member, ...]

    def names(self) -> list[str]:
        return [m.name for m in self.members]

    def by_name(self, name: str) -> Member | None:
        for member in self.members:
            if member.name == name:
                return member
        return None


def roster_from_dict(data: dict[str, Any]) -> Roster:
    members = tuple(
        Member(
            name=str(item["name"]),
            first=str(item["first"]),
            last=str(item["last"]),
            role=str(item["role"]),
        )
        for item in data["members"]
    )
    if not members:
        raise ValueError(
            "roster has no members; add each member as the city's own page prints the name"
        )
    return Roster(
        city=str(data["city"]),
        body=str(data["body"]),
        state=str(data.get("state", "")),
        sources=tuple(str(s) for s in data.get("sources", [])),
        members=members,
    )


def load_roster(path: Path) -> Roster:
    """Read a roster file. There is no default: every city brings its own roster."""
    return roster_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
