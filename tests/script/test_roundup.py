import pytest

from civiccast_brief.script.checker import check_script
from civiccast_brief.script.recap import Claim, Recap
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
