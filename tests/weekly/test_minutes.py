"""The Superior minutes parser, on the clerk's own text."""

from __future__ import annotations

from pathlib import Path

from civiccast_brief.weekly.minutes import (
    deciding_motions,
    parse_motions,
    present_members,
    votes_from_documents,
    votes_text,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "minutes"


def _pages(name: str) -> tuple[str, ...]:
    return tuple(FIXTURES.joinpath(name).read_text(encoding="utf-8").split("\f"))


def test_september_28_motions() -> None:
    motions = parse_motions(_pages("superior-2026-09-28.txt"))
    by_item = {motion.item: motion for motion in motions}
    assert set(by_item) == {"preliminary-matters-c", "consent-agenda", "public-hearing-a"}

    agenda = by_item["preliminary-matters-c"]
    assert agenda.mover == "Stephanie Miller"
    assert agenda.seconder == "Heather Cracraft"
    assert agenda.motion == "approve the agenda with no changes"
    assert (agenda.result, agenda.tally) == ("Passed", "6-0")

    fire_code = by_item["public-hearing-a"]
    assert fire_code.mover == "Mike Foster"
    assert fire_code.seconder == "Jenn Kaaoush"
    assert "International Fire Code" in fire_code.motion
    assert (fire_code.result, fire_code.tally) == ("Passed", "6-0")
    assert fire_code.page == 3


def test_october_12_budget_motion() -> None:
    motions = parse_motions(_pages("superior-2026-10-12.txt"))
    budget = next(m for m in motions if m.item == "public-hearing-a")
    assert budget.mover == "Lacis"
    assert budget.seconder == "Neal Shah"
    assert "2027 Budget" in budget.motion
    assert (budget.result, budget.tally) == ("Passed", "6-0")
    # The executive-session motions are procedural, and the item that decided the
    # budget is the one before them.
    assert all(m.procedural for m in motions if m.item.startswith("executive-session"))


def test_present_list_is_the_record_of_who_sat() -> None:
    assert present_members(_pages("superior-2026-09-28.txt")) == [
        "Mark Lacis",
        "Jenn Kaaoush",
        "Heather Cracraft",
        "Mike Foster",
        "Stephanie Miller",
        "Neal Shah",
    ]
    # "Absent:" is empty; the list below it is not part of the roll call.
    assert present_members(_pages("superior-2026-10-12.txt")) == [
        "Mark Lacis",
        "Heather Cracraft",
        "Mike Foster",
        "Jenn Kaaoush",
        "Stephanie Miller",
        "Neal Shah",
    ]


def test_votes_from_documents_uses_the_minutes_entry() -> None:
    data = {
        "documents": [
            {
                "kind": "minutes",
                "pages": [
                    {"page": n, "text": text}
                    for n, text in enumerate(_pages("superior-2026-09-28.txt"), start=1)
                ],
            }
        ]
    }
    text = votes_from_documents(data)
    assert "International Fire Code" in text
    assert "Moved by Mike Foster; seconded by Jenn Kaaoush. Passed 6-0" in text


def test_unpublished_minutes_read_as_empty() -> None:
    assert votes_from_documents({"documents": [{"kind": "packet", "pages": []}]}) == ""


def test_deciding_motion_skips_procedural_and_keeps_the_last() -> None:
    motions = parse_motions(_pages("superior-2026-10-12.txt"))
    deciding = deciding_motions(motions)
    assert "executive-session" not in {m.item for m in deciding}
    # Eight substantive decisions remain: agenda, consent, six budget resolutions.
    assert len(deciding) == 8
    assert "public-hearing-i" in {m.item for m in deciding}
    assert "2027 fee schedule" in votes_text(motions)
