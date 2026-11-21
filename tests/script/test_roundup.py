import json

import pytest

from civiccast_brief.script.checker import check_script
from civiccast_brief.script.recap import (
    Claim,
    Recap,
    channel_place,
    named_bodies,
    named_place,
)
from civiccast_brief.script.roundup import RoundupError, combine
from civiccast_brief.script.writer import ScriptLine, build_prompt


def recap(date, *texts, city="Longmont"):
    claims = tuple(
        Claim(id=f"c{i:02d}", topic="Item", text=t, timestamp="0:01:00", quote=t)
        for i, t in enumerate(texts, start=1)
    )
    return Recap(city=city, body="City Council", meeting_date=date, video_id="v", claims=claims)


def test_combine_prefixes_ids_and_dates():
    r = combine([recap("October 20, 2026", "A passed 6-1."), recap("October 27, 2026", "B.")])
    assert [c.id for c in r.claims] == ["m1-c01", "m2-c01"]
    assert r.claims[0].text == "[October 20, 2026 meeting] A passed 6-1."
    assert r.meeting_date == "October 20, 2026 and October 27, 2026"
    assert "WEEKLY ROUNDUP" in build_prompt(r)
    assert "WEEKLY ROUNDUP" not in build_prompt(recap("October 20, 2026", "A."))


def test_combine_puts_the_meetings_in_date_order():
    r = combine(
        [
            recap("October 20, 2026", "B."),
            recap("not a date", "C."),
            recap("October 6, 2026", "A."),
        ]
    )
    assert [c.text for c in r.claims] == [
        "[October 6, 2026 meeting] A.",
        "[October 20, 2026 meeting] B.",
        "[not a date meeting] C.",
    ]
    assert r.meeting_date == "October 6, 2026 and October 20, 2026 and not a date"


def test_combine_needs_two_recaps_of_one_city():
    with pytest.raises(RoundupError):
        combine([recap("October 20, 2026", "A.")])
    with pytest.raises(RoundupError):
        combine([recap("October 20, 2026", "A."), recap("October 27, 2026", "B.", city="Boulder")])


def test_a_board_the_site_carries_from_another_city_is_the_sites_own():
    """Louisville, 17 November 2026: its site carries the Boulder Valley school board, whose
    roster is headed "Boulder", so the week's roundup covers two cities and was refused."""
    board = Recap(
        city="Boulder",
        body="Board of Education",
        meeting_date="November 17, 2026",
        video_id="v",
        claims=(Claim("c01", "Item", "A.", "0:01:00", "A."),),
    )
    council = recap("November 17, 2026", "B.", city="Louisville")
    r = combine([board, council], city="Louisville", shared=[("Boulder", "Board of Education")])
    assert r.city == "Louisville"
    assert r.body == "Board of Education and City Council"
    assert len(r.claims) == 2

    # A recap that really belongs to another site is still refused: this "Boulder" council
    # recap is not a board Louisville's site carries.
    with pytest.raises(RoundupError):
        combine(
            [recap("November 17, 2026", "B.", city="Boulder"), council],
            city="Louisville",
            shared=[("Boulder", "Board of Education")],
        )


def board_and_council():
    """The 17 November 2026 roundup: Louisville's council and the school board its site carries,
    whose roster is headed "Boulder" and whose channel names "Boulder Valley"."""
    board = Recap(
        city="Boulder",
        body="Board of Education",
        meeting_date="November 17, 2026",
        video_id="v",
        claims=(Claim("c01", "Item", "A.", "0:01:00", "A."),),
    )
    council = recap("November 17, 2026", "B.", city="Louisville")
    return [board, council], [("Boulder", "Board of Education")]


def test_a_carried_board_is_named_with_its_own_place():
    """Live on 21 November 2026: the roundup said "Louisville Board of Education", a body that
    does not exist. It is the Boulder Valley school board."""
    recaps, shared = board_and_council()
    r = combine(
        recaps,
        city="Louisville",
        shared=shared,
        places={("boulder", "board of education"): "Boulder Valley"},
    )
    assert r.body == "Board of Education and City Council"
    assert r.body_label == "Boulder Valley Board of Education and Louisville City Council"
    assert named_bodies(r) == "Boulder Valley Board of Education and Louisville City Council"
    assert named_place(r) == r.body_label
    assert "Boulder Valley Board of Education and Louisville City Council" in build_prompt(r)


def test_a_carried_board_with_no_known_place_gets_no_city():
    recaps, shared = board_and_council()
    r = combine(recaps, city="Louisville", shared=shared)
    assert r.body_label == "Board of Education and Louisville City Council"
    assert "Louisville Board of Education" not in named_bodies(r)
    assert "Louisville Board of Education" not in build_prompt(r)


def test_a_one_city_roundup_keeps_the_labels_it_had():
    recaps = [
        recap("October 20, 2026", "A.", city="Louisville"),
        recap("October 27, 2026", "B.", city="Louisville"),
    ]
    r = combine(recaps, city="Louisville", shared=[("Boulder", "Board of Education")])
    assert r.body_label == ""
    assert named_bodies(r) == "City Council"
    assert named_place(r) == "Louisville City Council"
    assert "Meeting (from the recap header): City Council of Louisville, " in build_prompt(r)


def test_the_place_comes_from_the_meetings_own_record(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "dfQ44_yL_ac.info.json").write_text(
        json.dumps({"channel": "Boulder Valley School District Board of Education"}),
        encoding="utf-8",
    )
    board = Recap(
        city="Boulder",
        body="Board of Education",
        meeting_date="November 17, 2026",
        video_id="v",
        claims=(),
    )
    assert channel_place(board, tmp_path) == "Boulder Valley"


def test_the_checker_rejects_a_carried_board_named_with_the_sites_city():
    recaps, shared = board_and_council()
    r = combine(
        recaps,
        city="Louisville",
        shared=shared,
        places={("boulder", "board of education"): "Boulder Valley"},
    )
    wrong = check_script(r, [ScriptLine("S1", "We're covering the Louisville Board of Education.")])
    assert any(issue.code == "body_place" for issue in wrong.errors), wrong.render()
    right = check_script(
        r, [ScriptLine("S1", "We're covering the Boulder Valley Board of Education.")]
    )
    assert not any(issue.code == "body_place" for issue in right.errors), right.render()


def test_a_roundup_line_may_name_its_meeting_date_from_the_claim():
    r = combine([recap("October 20, 2026", "A passed 6-1."), recap("October 27, 2026", "B.")])
    lines = [
        ScriptLine(
            "S1",
            "Welcome to CivicCast Brief: Longmont City Council, October twentieth and twenty-seventh.",
        ),
        ScriptLine(
            "S2",
            "This episode is AI-generated from the public meeting video; check the official record.",
        ),
        ScriptLine("CHAPTER", "October twentieth meeting", ("m1-c01",)),
        ScriptLine("S1", "On October twentieth, A passed six to one.", ("m1-c01",)),
        ScriptLine("S2", "Find the meeting videos on the city's YouTube channel."),
    ]
    report = check_script(r, lines)
    assert report.passed, report.render()


def test_two_meetings_on_one_day_name_the_day_once():
    """Council and the Historic Preservation Commission both met on November 5: the date line
    said "November 5, 2026 and November 5, 2026"."""
    other = recap("November 5, 2026", "B.")
    other = Recap(
        other.city, "Historic Preservation Commission", other.meeting_date, "w", other.claims
    )
    r = combine([recap("November 3, 2026", "A."), recap("November 5, 2026", "C."), other])
    assert r.meeting_date == "November 3, 2026 and November 5, 2026"
