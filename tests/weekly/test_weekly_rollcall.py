"""The Oct. 26 landscape roll call: six yes votes, read from the video the clerk read."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from civiccast_brief.article.names import People, Person
from civiccast_brief.weekly import rollcall

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "minutes"


@dataclass
class _Segment:
    start: float
    text: str


@dataclass
class _Page:
    text: str


@dataclass
class _Member:
    name: str


@dataclass
class _Roster:
    members: tuple[_Member, ...]


def _people() -> People:
    names = [
        ("Mark", "Lacis", "Mayor"),
        ("Jenn", "Kaaoush", "Mayor Pro Tem"),
        ("Heather", "Cracraft", "Council Member"),
        ("Mike", "Foster", "Council Member"),
        ("Stephanie", "Miller", "Council Member"),
        ("Neal", "Shah", "Council Member"),
    ]
    return People(
        persons=tuple(
            Person(
                name=f"{first} {last}",
                first=first,
                last=last,
                role=role,
                source="roster.json",
                evidence=f'"name": "{first} {last}"',
            )
            for first, last, role in names
        ),
        text="",
    )


def _segments() -> list[_Segment]:
    rows = json.loads(
        (FIXTURES / "superior-2026-10-26-landscape-rollcall.json").read_text(encoding="utf-8")
    )
    return [_Segment(float(row["start"]), str(row["text"])) for row in rows]


def _present() -> dict[str, _Page]:
    return {
        "p1": _Page(
            "Present:\nMayor Mark Lacis\nCouncil Member Heather Cracraft\n"
            "Council Member Mike Foster\nCouncil Member Jenn Kaaoush\n"
            "Council Member Stephanie Miller\nCouncil Member Neal Shah\n\nAbsent:\n"
        )
    }


def test_landscape_roll_call_is_six_yes() -> None:
    people = _people()
    roster = _Roster(tuple(_Member(p.name) for p in people.persons))
    names = rollcall.record_members(roster, people, _present())
    assert "Mike Foster" in names

    calls = rollcall.read_roll_call(_segments(), people, names)
    assert len(calls) == 1
    call = calls[0]
    assert call.timestamp == "1:46:16"
    assert call.tally == "6-0"
    assert set(call.yes) == {
        "Mike Foster",
        "Jenn Kaaoush",
        "Mark Lacis",
        "Neal Shah",
        "Stephanie Miller",
        "Heather Cracraft",
    }
    assert call.no == ()
    assert call.names_certain is True
    assert "1:46:16: 6-0" in rollcall.roll_call_text(calls)


def test_a_roll_call_with_an_unreadable_name_is_not_attributed() -> None:
    segments = [
        _Segment(100.0, "Roll call vote, please."),
        _Segment(104.0, "Councilmember Zzz?"),
        _Segment(105.0, "Yes."),
        _Segment(106.0, "Passes."),
    ]
    # Two members, one readable slot, one garbled name: no certain mapping, so no names.
    calls = rollcall.read_roll_call(segments, _people(), ["Mark Lacis", "Neal Shah"])
    assert calls == []
