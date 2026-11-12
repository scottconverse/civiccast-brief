import itertools
import json
from pathlib import Path

import pytest

from civiccast_brief.audio.engines import FakeEngine, LineTiming, RenderResult
from civiccast_brief.audio.errors import FinishError
from civiccast_brief.audio.ffmpeg import probe_duration, run_ffmpeg, tools_available
from civiccast_brief.audio.finish import (
    SPEAKER_NAMES,
    Cue,
    chapter_marks,
    chapters_document,
    finish_episode,
    measure_loudness,
    normalize_loudness,
    parse_ebur128_summary,
    vtt_document,
    vtt_timestamp,
)
from civiccast_brief.audio.lines import Chapter, Line, Script, parse_script

needs_ffmpeg = pytest.mark.skipif(not tools_available(), reason="ffmpeg/ffprobe not on PATH")

EPISODE_SCRIPT = """\
[CHAPTER Intro]
[S1] Welcome to the Longmont council recap for October twentieth.
[S2] This episode is AI-generated from the public meeting video.
[CHAPTER Votes]
[S1] Ordinance 2026-69 passed five to two.
[S2] The amendments need a fire plan by March first, twenty twenty-seven.
[S1] That wraps it up for today.
"""


# --- pure: timestamps, cues, chapters -------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "00:00:00.000"), (3.4567, "00:00:03.457"), (3725.5, "01:02:05.500")],
)
def test_vtt_timestamp_formats_hours_minutes_seconds_and_milliseconds(
    seconds: float, expected: str
) -> None:
    assert vtt_timestamp(seconds) == expected


def test_vtt_document_has_header_voice_tags_and_escaped_text() -> None:
    cues = [
        Cue(0.0, 2.1, "Maya", "Welcome & good evening."),
        Cue(2.45, 6.05, "Theo", "Under 5 < 10 > 0 is ok."),
    ]
    text = vtt_document(cues)
    assert text.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:02.100\n<v Maya>Welcome &amp; good evening.</v>" in text
    assert "00:00:02.450 --> 00:00:06.050\n<v Theo>Under 5 &lt; 10 &gt; 0 is ok.</v>" in text
    assert text.endswith("\n")


def test_vtt_document_rejects_overlapping_or_reversed_cues() -> None:
    with pytest.raises(FinishError, match="out of order"):
        vtt_document([Cue(0.0, 3.0, "Maya", "a"), Cue(2.5, 4.0, "Theo", "b")])
    with pytest.raises(FinishError, match="out of order"):
        vtt_document([Cue(1.0, 1.0, "Maya", "zero length")])


def test_chapter_marks_first_chapter_starts_at_zero_and_others_at_their_line() -> None:
    script = Script(
        lines=(Line(2, "S1", "a"), Line(3, "S2", "b"), Line(5, "S1", "c")),
        chapters=(Chapter("Intro", 0), Chapter("Votes", 2)),
    )
    marks = chapter_marks(script, [0.0, 4.0, 9.5])
    assert [(mark.start, mark.title) for mark in marks] == [(0.0, "Intro"), (9.5, "Votes")]


def test_first_chapter_absorbs_lines_before_its_mark() -> None:
    script = parse_script("[S1] Before any mark.\n[CHAPTER Late mark]\n[S2] After it.\n")
    starts = [0.0, 3.0]
    marks = chapter_marks(script, starts)
    assert [(mark.start, mark.title) for mark in marks] == [(0.0, "Late mark")]


def test_chapters_document_matches_the_podcasting_20_json_shape() -> None:
    marks = chapter_marks(
        Script((Line(1, "S1", "a"), Line(2, "S2", "b")), (Chapter("Intro", 0), Chapter("B", 1))),
        [0.0, 5.25],
    )
    document = json.loads(json.dumps(chapters_document(marks)))
    assert document["version"] == "1.2.0"
    assert document["chapters"] == [
        {"startTime": 0.0, "title": "Intro"},
        {"startTime": 5.25, "title": "B"},
    ]
    starts = [chapter["startTime"] for chapter in document["chapters"]]
    assert starts == sorted(starts)
    assert all(isinstance(chapter["title"], str) for chapter in document["chapters"])


def test_finish_refuses_a_render_whose_line_count_does_not_match_the_script(
    tmp_path: Path,
) -> None:
    script = parse_script("[S1] One.\n[S2] Two.\n")
    render = RenderResult(
        wav=tmp_path / "voice.wav", engine="fake", timing="exact", lines=(), duration=0.0
    )
    with pytest.raises(FinishError, match="0 line timings for 2 lines"):
        finish_episode(render, script, tmp_path)


def test_finish_refuses_a_speaker_without_a_display_name(tmp_path: Path) -> None:
    script = parse_script("[S1] One.\n")
    render = RenderResult(
        wav=tmp_path / "voice.wav",
        engine="fake",
        timing="exact",
        lines=(LineTiming(1, "S1", 0.0, 1.0),),
        duration=1.0,
    )
    with pytest.raises(FinishError, match="no display names"):
        finish_episode(render, script, tmp_path, speaker_names={"S1": "Maya"})


def test_ebur128_summary_parser_reads_the_three_values() -> None:
    summary = (
        "[Parsed_ebur128_0 @ 0x1] Summary:\n\n"
        "  Integrated loudness:\n    I:         -16.2 LUFS\n    Threshold: -26.3 LUFS\n\n"
        "  Loudness range:\n    LRA:         4.1 LU\n    Threshold: -36.3 LUFS\n\n"
        "  True peak:\n    Peak:       -1.9 dBFS\n"
    )
    loudness = parse_ebur128_summary(summary)
    assert (loudness.integrated_lufs, loudness.lra, loudness.true_peak_dbtp) == (-16.2, 4.1, -1.9)


def test_speaker_names_default_to_maya_and_theo() -> None:
    assert SPEAKER_NAMES == {"S1": "Maya", "S2": "Theo"}


# --- ffmpeg: a real episode from FakeEngine -------------------------------------------------


@needs_ffmpeg
def test_fake_episode_has_consistent_timing_chapters_and_transcript(tmp_path: Path) -> None:
    script = parse_script(EPISODE_SCRIPT)
    out = tmp_path / "episode"
    render = FakeEngine().render(list(script.lines), out / "work" / "voice.wav")
    files = finish_episode(render, script, out)

    assert files.mp3.is_file() and files.chapters_json.is_file() and files.transcript_vtt.is_file()

    # Duration: five lines of tone plus four 0.35 s gaps, plus at most encoder padding.
    expected = render.lines[-1].end
    assert abs(files.duration - expected) < 0.1

    # chapters.json: version, ordered start times, first chapter at zero.
    chapters = json.loads(files.chapters_json.read_text(encoding="utf-8"))
    assert chapters["version"] == "1.2.0"
    assert [chapter["title"] for chapter in chapters["chapters"]] == ["Intro", "Votes"]
    assert chapters["chapters"][0]["startTime"] == 0.0
    assert chapters["chapters"][1]["startTime"] == pytest.approx(render.lines[2].start, abs=1e-3)

    # VTT: one cue per spoken line, in order, with the right voice tags.
    vtt = files.transcript_vtt.read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT\n")
    assert vtt.count(" --> ") == len(script.lines)
    assert vtt.count("<v Maya>") == 3 and vtt.count("<v Theo>") == 2
    assert len(files.cues) == len(script.lines)
    for earlier, later in itertools.pairwise(files.cues):
        assert earlier.start < earlier.end <= later.start

    # Loudness of the delivered MP3, measured by ffmpeg ebur128.
    measured = measure_loudness(files.mp3)
    assert abs(measured.integrated_lufs - (-16.0)) <= 1.0
    assert measured.true_peak_dbtp <= -1.0


@needs_ffmpeg
def test_intro_and_outro_shift_the_timeline_but_not_the_chapter_zero(tmp_path: Path) -> None:
    intro = tmp_path / "intro.mp3"
    outro = tmp_path / "outro.mp3"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=2",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(intro),
        ]
    )
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=1", str(outro)])
    script = parse_script(EPISODE_SCRIPT)
    out = tmp_path / "episode"
    render = FakeEngine().render(list(script.lines), out / "work" / "voice.wav")
    files = finish_episode(render, script, out, intro=intro, outro=outro)

    intro_len = probe_duration(intro)
    assert files.cues[0].start == pytest.approx(intro_len, abs=0.01)
    assert files.chapters[0].start == 0.0
    assert files.chapters[1].start == pytest.approx(files.cues[2].start, abs=1e-3)
    total = intro_len + render.duration + probe_duration(outro)
    assert abs(files.duration - total) < 0.15


@needs_ffmpeg
def test_silent_program_cannot_be_normalised(tmp_path: Path) -> None:
    silent = tmp_path / "silent.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", str(silent)])
    with pytest.raises(FinishError, match="silent"):
        normalize_loudness(silent, tmp_path / "out.wav")


def test_a_chapter_mark_title_starts_with_a_capital_letter() -> None:
    """The model sometimes writes a chapter title in lower case; the title is shown, not spoken."""
    script = Script((Line(1, "S1", "a"),), (Chapter("public comment", 0),))
    assert [mark.title for mark in chapter_marks(script, [0.0])] == ["Public comment"]
