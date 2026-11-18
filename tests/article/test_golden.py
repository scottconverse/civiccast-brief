"""Golden-fact scoring, on Superior fixtures built from the minutes."""

from __future__ import annotations

from pathlib import Path

from civiccast_brief.article import golden

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


def _fixture(name: str) -> golden.GoldenFixture:
    return golden.load_fixture_file(FIXTURES / name)


def _story(body: str) -> golden.StoryText:
    return golden.StoryText(headline="Superior council votes", body=body)


def test_a_correct_story_passes() -> None:
    fixture = _fixture("superior-2026-09-28.json")
    story = _story(
        "The Superior Town Council passed an ordinance adopting by reference the 2024 "
        "International Fire Code. Mike Foster moved the ordinance and Jenn Kaaoush "
        "seconded it. The vote was 6-0."
    )
    score = golden.score(story, fixture)
    assert score.pass_ is True, score.failures
    assert score.counts["correct"] == 4


def test_a_wrong_mover_fails() -> None:
    fixture = _fixture("superior-2026-09-28.json")
    story = _story(
        "The council adopted the 2024 International Fire Code. Stephanie Miller moved the "
        "ordinance and Jenn Kaaoush seconded it. The vote was 6-0."
    )
    score = golden.score(story, fixture)
    assert score.pass_ is False
    assert any(v.kind == "mover" and v.verdict == "wrong" for v in score.verdicts)


def test_a_wrong_tally_fails() -> None:
    fixture = _fixture("superior-2026-09-28.json")
    story = _story(
        "The council adopted the 2024 International Fire Code. Mike Foster moved it and "
        "Jenn Kaaoush seconded it. The vote was 2-0."
    )
    score = golden.score(story, fixture)
    assert score.pass_ is False
    assert any(v.kind == "result" and v.verdict == "wrong" for v in score.verdicts)


def test_an_unstated_required_fact_fails() -> None:
    fixture = _fixture("superior-2026-09-28.json")
    story = _story("The council adopted the 2024 International Fire Code. The vote was 6-0.")
    score = golden.score(story, fixture)
    assert score.pass_ is False
    assert any(v.kind == "mover" and v.verdict == "not-stated" for v in score.verdicts)


def test_a_caption_spelling_is_wrong() -> None:
    fixture = _fixture("superior-2026-09-28.json")
    story = _story(
        "The council adopted the 2024 International Fire Code. Mike Foster moved it and "
        "Mayor Pro Tem Craigcraft seconded it. The vote was 6-0."
    )
    score = golden.score(story, fixture)
    assert score.pass_ is False
    seconder = next(v for v in score.verdicts if v.kind == "seconder")
    assert seconder.verdict == "wrong"
    assert "Cracraft" in seconder.reason


def test_october_12_budget_fixture_passes() -> None:
    fixture = _fixture("superior-2026-10-12.json")
    story = _story(
        "The council approved the town's 2027 Budget and its Appropriations. Mayor Mark "
        "Lacis moved the resolution and Neal Shah seconded it. It carried 6-0."
    )
    score = golden.score(story, fixture)
    assert score.pass_ is True, score.failures


def test_format_verdicts_names_every_fact() -> None:
    fixture = _fixture("superior-2026-09-28.json")
    text = golden.format_verdicts(golden.score(_story("Nothing happened."), fixture))
    assert "VERDICT" in text
    assert text.count("\n") >= 4


def test_a_failing_story_passes_after_one_rewrite() -> None:
    """The rewrite loop: a draft that contradicts a required fact fails, the corrected one passes."""
    fixture = _fixture("superior-2026-10-12.json")
    draft = _story(
        "The council approved a $41 million budget. Mark Lacis made the motion and the "
        "council passed it 5-1."
    )
    first = golden.score(draft, fixture)
    assert first.pass_ is False
    rewrite = _story(
        "The council approved the town's 2027 Budget and its Appropriations 6-0. Mark Lacis "
        "moved the resolution and Neal Shah seconded it."
    )
    second = golden.score(rewrite, fixture)
    assert second.pass_ is True, second.failures
