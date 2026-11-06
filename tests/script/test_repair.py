from test_checker import RECAP, good_script

from civiccast_brief.script.checker import check_script, drop_unsupported
from civiccast_brief.script.writer import DISCLAIMER, ScriptLine


def test_drops_a_line_with_a_new_name_and_then_passes():
    lines = good_script()
    lines.insert(4, ScriptLine("S2", "The Thompson farmhouse vote failed.", ("c02",)))
    assert not check_script(RECAP, lines).passed

    repaired, dropped = drop_unsupported(RECAP, lines)

    assert [d.text for d in dropped] == ["The Thompson farmhouse vote failed."]
    assert check_script(RECAP, repaired).passed
    assert repaired == good_script()


def test_drops_new_number_and_uncited_fact_lines():
    lines = good_script()
    lines.insert(4, ScriptLine("S1", "A member moved to amend condition three.", ("c02",)))
    lines.insert(5, ScriptLine("S2", "That's the meeting."))
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert len(dropped) == 2
    assert check_script(RECAP, repaired).passed


def test_drops_a_chapter_left_empty():
    lines = good_script()
    # A chapter whose only speech line gets dropped must go too.
    # Inserted before the existing chapter, so after the drop two chapters sit together.
    lines.insert(2, ScriptLine("CHAPTER", "Ordinance twenty twenty-six dash sixty-nine", ("c02",)))
    lines.insert(3, ScriptLine("S1", "The Thompson farmhouse vote failed.", ("c02",)))
    repaired, _ = drop_unsupported(RECAP, lines)
    chapters = [line for line in repaired if line.is_chapter]
    assert len(chapters) == 1


def test_never_drops_intro_disclaimer_or_outro():
    lines = good_script()
    lines[0] = ScriptLine("S1", "CivicCast Brief, Longmont, with the Thompson news.", ("c01",))
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert repaired[0] == lines[0]
    assert repaired[1].text == DISCLAIMER
    assert dropped == []
    assert not check_script(RECAP, repaired).passed  # left for the writer or a human


def test_drops_the_question_and_reactions_a_drop_leaves_behind():
    lines = good_script()
    lines[2:2] = [
        ScriptLine("CHAPTER", "Bonds", ("c02",)),
        ScriptLine("S2", "Right."),
        ScriptLine("S2", "What did the speaker say about the bonds?"),
        ScriptLine("S1", "The Thompson bonds failed.", ("c02",)),
        ScriptLine("CHAPTER", "Ordinance again", ("c02",)),
    ]
    lines.insert(9, ScriptLine("S1", "Ask me anything?"))
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert [d.text for d in dropped] == [
        "Bonds",
        "The Thompson bonds failed.",
        "Right.",
        "What did the speaker say about the bonds?",
        "Ordinance again",
        "Ask me anything?",
    ]
    assert repaired == good_script()
    assert check_script(RECAP, repaired).passed


def test_keeps_a_question_that_its_next_line_answers():
    lines = good_script()
    lines.insert(3, ScriptLine("S2", "What happened with the ordinance?"))
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert dropped == [] and repaired == lines


def test_keeps_the_question_that_hands_over_to_the_outro():
    lines = good_script()
    lines.insert(6, ScriptLine("S1", "Theo, where can people watch it?"))
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert dropped == [] and repaired == lines
