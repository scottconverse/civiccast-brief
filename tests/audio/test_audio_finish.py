import array
import itertools
import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from civiccast_brief.audio import finish as finish_module
from civiccast_brief.audio.engines import FakeEngine, LineTiming, RenderResult
from civiccast_brief.audio.errors import FinishError
from civiccast_brief.audio.ffmpeg import probe_duration, run_ffmpeg, tools_available
from civiccast_brief.audio.finish import (
    SAMPLE_RATE,
    SPEAKER_NAMES,
    Cue,
    Loudness,
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


# --- true-peak limiter and MP3 headroom (stubbed ffmpeg) ------------------------------------


@dataclass
class _FakeProc:
    stderr: str = ""
    stdout: str = ""


def _one_line_render(tmp_path: Path) -> RenderResult:
    return RenderResult(
        wav=tmp_path / "voice.wav",
        engine="fake",
        timing="exact",
        lines=(LineTiming(1, "S1", 0.0, 1.0),),
        duration=1.0,
    )


def test_limiter_filter_gain_ceiling_and_settings() -> None:
    graph = finish_module.limiter_filter(3.0, -5.5)
    assert graph.startswith("volume=3.000dB,alimiter=")
    assert "level=disabled" in graph
    assert f"attack={finish_module.LIMITER_ATTACK_MS}" in graph
    assert f"release={finish_module.LIMITER_RELEASE_MS}" in graph
    # Without delay compensation the limiter shifts the audio by its lookahead (5 ms measured
    # on real episodes), which would slide chapters and transcripts against the audio.
    assert "latency=true" in graph
    # The ceiling is a linear sample-peak limit, so -5.5 dBFS is 10 ** (-5.5 / 20).
    match = re.search(r"limit=([\d.]+)", graph)
    assert match is not None
    assert float(match.group(1)) == pytest.approx(10 ** (-5.5 / 20), abs=1e-5)


def _write_click_wav(path: Path, *, position: int = 17640, seconds: float = 1.0) -> None:
    """A mono 16-bit WAV of silence with a three-sample click at `position`."""
    frames = array.array("h", [0]) * int(SAMPLE_RATE * seconds)
    for offset in range(-1, 2):
        frames[position + offset] = 30000 - abs(offset) * 1000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(frames.tobytes())


def _read_peak_position(path: Path) -> tuple[int, int]:
    """(sample index of the largest absolute sample, number of samples)."""
    with wave.open(str(path), "rb") as wav:
        frames = array.array("h", wav.readframes(wav.getnframes()))
    peak = max(range(len(frames)), key=lambda index: abs(frames[index]))
    return peak, len(frames)


@needs_ffmpeg
def test_limiter_keeps_a_click_at_its_sample_and_the_length_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "click.wav"
    out = tmp_path / "limited.wav"
    _write_click_wav(source)
    # The click is at 0.915 full scale, over the -1.5 dBFS ceiling, so the limiter really works.
    finish_module.apply_peak_limiter(source, out, gain_db=0.0, ceiling_db=-1.5)

    position, length = _read_peak_position(out)
    _, source_length = _read_peak_position(source)
    assert abs(position - 17640) <= 1
    assert length == source_length


@needs_ffmpeg
def test_limiter_is_a_no_op_when_the_signal_is_under_the_ceiling(tmp_path: Path) -> None:
    source = tmp_path / "quiet.wav"
    out = tmp_path / "limited.wav"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=1",
            "-af",
            "volume=-10dB",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(source),
        ]
    )
    finish_module.apply_peak_limiter(source, out, gain_db=0.0, ceiling_db=-1.5)

    with wave.open(str(source), "rb") as original, wave.open(str(out), "rb") as limited:
        assert limited.readframes(limited.getnframes()) == original.readframes(
            original.getnframes()
        )


def _stub_mastering(monkeypatch: pytest.MonkeyPatch, measurements: list[Loudness]) -> list[float]:
    """Stub the ffmpeg stages and record the limiter ceiling each attempt was rendered with."""
    ceilings: list[float] = []
    remaining = list(measurements)

    def fake_normalize(program: Path, out_wav: Path, *, true_peak: float = -1.5) -> None:
        out_wav.write_text("normalised", encoding="utf-8")

    def fake_solve(program: Path, out_wav: Path, *, ceiling_db: float, target_lufs: float) -> float:
        ceilings.append(ceiling_db)
        out_wav.write_text(out_wav.name, encoding="utf-8")
        return 2.0

    def fake_encode(master: Path, out_mp3: Path) -> None:
        out_mp3.write_text(out_mp3.name, encoding="utf-8")

    def fake_measure(path: Path) -> Loudness:
        return remaining.pop(0)

    monkeypatch.setattr(finish_module, "normalize_loudness", fake_normalize)
    monkeypatch.setattr(finish_module, "solve_limiter_gain", fake_solve)
    monkeypatch.setattr(finish_module, "encode_mp3", fake_encode)
    monkeypatch.setattr(finish_module, "measure_loudness", fake_measure)
    monkeypatch.setattr(finish_module, "probe_duration", lambda path: 3.0)
    return ceilings


def test_master_keeps_the_first_attempt_when_it_meets_both_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ceilings = _stub_mastering(monkeypatch, [Loudness(-16.1, -1.7, 4.0)])
    work = tmp_path / "work"
    work.mkdir()
    master, mp3, loudness = finish_module.master_for_mp3(
        tmp_path / "program.wav", work, tmp_path / "episode.mp3"
    )

    assert ceilings == [-1.5]
    assert loudness == Loudness(-16.1, -1.7, 4.0)
    assert (tmp_path / "episode.mp3").read_text(encoding="utf-8") == "episode-1.mp3"
    assert master.read_text(encoding="utf-8") == "master-1.wav"
    assert mp3 == tmp_path / "episode.mp3"


def test_master_lowers_the_ceiling_by_the_mp3_overshoot_and_keeps_the_passing_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # First attempt overshoots by 2.6 dB above the -1.6 dB peak margin; the retry passes.
    ceilings = _stub_mastering(monkeypatch, [Loudness(-16.0, 1.0, 5.0), Loudness(-16.2, -1.8, 4.0)])
    work = tmp_path / "work"
    work.mkdir()
    _, _mp3, loudness = finish_module.master_for_mp3(
        tmp_path / "program.wav", work, tmp_path / "episode.mp3"
    )

    # -1.5 - (1.0 - -1.6) - 0.1 = -4.2 dBFS.
    assert ceilings == pytest.approx([-1.5, -4.2])
    assert loudness.true_peak_dbtp == -1.8
    assert (tmp_path / "episode.mp3").read_text(encoding="utf-8") == "episode-2.mp3"
    assert (work / "master.wav").read_text(encoding="utf-8") == "master-2.wav"
    assert not (work / "master-1.wav").exists()
    assert not (work / "episode-1.mp3").exists()


def test_master_gives_up_and_the_peak_ceiling_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # None clears -1.5 dBTP, so the lowest peak is kept even though it is not the loudest.
    _stub_mastering(
        monkeypatch,
        [
            Loudness(-16.0, -1.0, 5.0),
            Loudness(-16.4, -1.2, 4.5),
            Loudness(-17.0, -1.4, 4.0),
            Loudness(-16.8, -1.3, 4.2),
        ],
    )
    work = tmp_path / "work"
    work.mkdir()
    _, _, loudness = finish_module.master_for_mp3(
        tmp_path / "program.wav", work, tmp_path / "episode.mp3"
    )
    assert loudness.true_peak_dbtp == -1.4


def test_pick_attempt_prefers_a_peak_under_the_ceiling_then_the_loudest_close_to_target(
    tmp_path: Path,
) -> None:
    attempts = [
        (tmp_path / "m1", tmp_path / "e1", Loudness(-15.0, -0.5, 5.0)),
        (tmp_path / "m2", tmp_path / "e2", Loudness(-16.4, -1.8, 4.0)),
        (tmp_path / "m3", tmp_path / "e3", Loudness(-16.1, -1.6, 4.2)),
    ]
    _, mp3, _ = finish_module.pick_attempt(attempts)
    assert mp3.name == "e3"


@needs_ffmpeg
def test_finish_episode_delivers_an_mp3_within_both_targets(tmp_path: Path) -> None:
    script = parse_script(EPISODE_SCRIPT)
    out = tmp_path / "episode"
    render = FakeEngine().render(list(script.lines), out / "work" / "voice.wav")
    files = finish_episode(render, script, out)

    measured = measure_loudness(files.mp3)
    assert abs(measured.integrated_lufs - (-16.0)) <= 0.5
    assert measured.true_peak_dbtp <= -1.5


def test_a_chapter_mark_title_starts_with_a_capital_letter() -> None:
    """The model sometimes writes a chapter title in lower case; the title is shown, not spoken."""
    script = Script((Line(1, "S1", "a"),), (Chapter("public comment", 0),))
    assert [mark.title for mark in chapter_marks(script, [0.0])] == ["Public comment"]
