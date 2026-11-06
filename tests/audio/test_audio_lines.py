from pathlib import Path

import pytest

from civiccast_brief.audio.errors import ScriptError
from civiccast_brief.audio.lines import parse_script, read_script


def test_parses_speakers_text_and_source_line_numbers() -> None:
    text = "[S1] Good evening.\n\n[S2] Right, and the vote was 5 to 2.\n"
    script = parse_script(text)
    assert [(line.number, line.speaker, line.text) for line in script.lines] == [
        (1, "S1", "Good evening."),
        (3, "S2", "Right, and the vote was 5 to 2."),
    ]
    assert script.chapters == ()


def test_chapter_mark_points_at_the_next_spoken_line_and_is_not_spoken() -> None:
    text = (
        "[S1] Intro line.\n"
        "[CHAPTER Board action]\n"
        "[S2] Second line.\n"
        "[S1] Third line.\n"
        "[CHAPTER Public comment]\n"
        "[S2] Fourth line.\n"
    )
    script = parse_script(text)
    assert len(script.lines) == 4
    assert all("CHAPTER" not in line.text for line in script.lines)
    assert [(c.title, c.line_index) for c in script.chapters] == [
        ("Board action", 1),
        ("Public comment", 3),
    ]


def test_chapter_at_the_top_points_at_line_zero() -> None:
    script = parse_script("[CHAPTER Intro]\n[S1] Hello.\n")
    assert [(c.title, c.line_index) for c in script.chapters] == [("Intro", 0)]


def test_brackets_inside_spoken_text_are_text() -> None:
    script = parse_script("[S1] The vote was [unanimous], per the clerk.\n")
    assert script.lines[0].text == "The vote was [unanimous], per the clerk."


def test_crlf_line_endings_and_surrounding_spaces_are_accepted() -> None:
    script = parse_script("  [S1] One.  \r\n[CHAPTER Two]\r\n[S2] Two.\r\n")
    assert [line.text for line in script.lines] == ["One.", "Two."]
    assert script.chapters[0].line_index == 1


@pytest.mark.parametrize(
    "text",
    [
        "[S3] Wrong speaker.\n",
        "A stage direction with no speaker tag.\n",
        "[S1]\n",
        "[CHAPTER]\n[S1] Untitled chapter.\n",
        "# Heading\n[S1] Hello.\n",
    ],
)
def test_anything_that_is_not_a_speaker_or_chapter_line_is_rejected(text: str) -> None:
    with pytest.raises(ScriptError, match="line 1"):
        parse_script(text)


def test_chapter_with_no_spoken_line_after_it_is_rejected() -> None:
    with pytest.raises(ScriptError, match="no spoken line after it"):
        parse_script("[S1] Hello.\n[CHAPTER Orphan]\n")


def test_two_chapter_marks_in_a_row_are_rejected() -> None:
    with pytest.raises(ScriptError, match="has no spoken line before the next chapter"):
        parse_script("[CHAPTER A]\n[CHAPTER B]\n[S1] Hello.\n")


def test_script_with_no_spoken_lines_is_rejected() -> None:
    with pytest.raises(ScriptError, match="no spoken lines"):
        parse_script("\n   \n")


def test_read_script_tolerates_a_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "script.txt"
    path.write_bytes("﻿[S1] Maya speaks.\n[S2] Theo answers.\n".encode())
    assert [line.speaker for line in read_script(path).lines] == ["S1", "S2"]


def test_read_script_rejects_text_that_is_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "script.txt"
    path.write_bytes(b"[S1] caf\xe9 in latin-1\n")
    with pytest.raises(ScriptError, match="not UTF-8"):
        read_script(path)


def test_read_script_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ScriptError, match="cannot read script"):
        read_script(tmp_path / "missing.txt")
