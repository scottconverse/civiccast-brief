"""The written episode title: checked like a headline, rewritten once, else no title."""

from __future__ import annotations

import json

from civiccast_brief.article.title import Title, check_title, write_title
from civiccast_brief.script.recap import Claim, Recap

RECAP = Recap(
    city="Longmont",
    body="City Council and Planning and Zoning Commission",
    meeting_date="October 6, 2026 and October 20, 2026",
    video_id="roundup",
    claims=(
        Claim(
            id="m1-c01",
            topic="October 6, 2026: Rezoning",
            text="[October 6, 2026 meeting] The commission recommended the Sunset rezoning 5-0.",
            timestamp="0:40:00",
            quote="five to zero",
        ),
        Claim(
            id="m2-c01",
            topic="October 20, 2026: Annexation",
            text="[October 20, 2026 meeting] The council approved the St. Vrain annexation 6-1.",
            timestamp="1:02:03",
            quote="six to one",
        ),
    ),
)


def answer(title: str, ids: list[str]) -> str:
    return json.dumps({"title": title, "claim_ids": ids})


def test_a_title_built_from_its_claims_passes() -> None:
    title = Title("Council approves St. Vrain annexation 6-1", ("m2-c01",))
    assert check_title(RECAP, title) == []


def test_a_title_with_a_name_or_number_its_claims_lack_fails() -> None:
    codes = {i.code for i in check_title(RECAP, Title("Mayor Peck wins 7-0 vote", ("m2-c01",)))}
    assert codes == {"name", "number"}
    # The commission's 5-0 is a claim, but not one this title cites.
    issues = check_title(RECAP, Title("Council votes 5-0", ("m2-c01",)))
    assert [i.message for i in issues] == [
        "number 0 is not in the cited claims",
        "number 5 is not in the cited claims",
    ]


def test_a_title_without_claims_or_too_long_fails() -> None:
    assert [i.code for i in check_title(RECAP, Title("Council meets", ()))] == ["uncited"]
    long = Title(" ".join(["council"] * 15), ("m2-c01",))
    assert [i.code for i in check_title(RECAP, long)] == ["length"]


def test_write_title_rewrites_once_with_the_problems() -> None:
    prompts: list[str] = []
    answers = iter(
        [
            answer("Council approves annexation 7-0", ["m2-c01"]),
            answer("Council approves annexation 6-1", ["m2-c01"]),
        ]
    )

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    title = write_title(RECAP, complete)
    assert title == Title("Council approves annexation 6-1", ("m2-c01",))
    assert "number 7 is not in the cited claims" in prompts[1]


def test_write_title_gives_none_when_both_drafts_fail() -> None:
    assert write_title(RECAP, lambda _: answer("Mayor Peck approves it", ["m2-c01"])) is None
    assert write_title(RECAP, lambda _: "no json here") is None
