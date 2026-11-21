from test_checker import RECAP, VOTE_IDS, VOTE_RECAP, good_script

from civiccast_brief.script.checker import check_script, drop_unsupported
from civiccast_brief.script.writer import ScriptLine


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


def test_never_drops_intro_or_outro():
    lines = good_script()
    lines[0] = ScriptLine("S1", "CivicCast Brief, Longmont, with the Thompson news.", ("c01",))
    lines[-1] = ScriptLine("S2", "That's all.")
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert repaired[0] == lines[0]
    assert repaired[-1] == lines[-1]
    assert dropped == []
    assert not check_script(RECAP, repaired).passed  # left for the writer or a human


def test_drops_the_question_and_reactions_a_drop_leaves_behind():
    lines = good_script()
    lines[3:3] = [
        ScriptLine("CHAPTER", "Bonds", ("c02",)),
        ScriptLine("S1", "Right."),
        ScriptLine("S2", "What did the speaker say about the bonds?"),
        ScriptLine("S2", "The Thompson bonds failed.", ("c02",)),
        ScriptLine("CHAPTER", "Ordinance again", ("c02",)),
    ]
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert [d.text for d in dropped] == [
        "Bonds",
        "The Thompson bonds failed.",
        "What did the speaker say about the bonds?",
        "Ordinance again",
    ]
    assert check_script(RECAP, repaired).passed


def test_keeps_a_question_that_its_next_line_answers():
    lines = good_script()
    lines.insert(2, ScriptLine("S1", "What happened with the ordinance?"))
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert dropped == [] and repaired == lines


def test_keeps_the_question_that_hands_over_to_the_outro():
    lines = good_script()
    lines.insert(4, ScriptLine("S1", "Alex, where can people watch it?"))
    repaired, dropped = drop_unsupported(RECAP, lines)
    assert dropped == [] and repaired == lines


def test_a_partial_vote_is_reduced_to_the_count_instead_of_ending_the_script():
    """A partial-vote error must not cost a meeting its script. The repair path reduces the
    sentence to the cited tally, drops the partial names, invents none, and passes."""
    lines = [
        ScriptLine("S1", "CivicCast Brief, Boulder."),
        ScriptLine(
            "S2",
            "Schuchard, Speer, Benjamin and Brockett voted yes. Adams and Kaplan voted no.",
            VOTE_IDS,
        ),
        ScriptLine("S2", "Watch the meeting video on the city's YouTube channel.", ("c11",)),
    ]
    before = check_script(VOTE_RECAP, lines)
    assert [i.code for i in before.errors] == ["partial_vote", "partial_vote"]

    repaired, dropped = drop_unsupported(VOTE_RECAP, lines)

    assert dropped == []
    assert repaired[1].text == "The vote was five to three."
    assert check_script(VOTE_RECAP, repaired).passed
