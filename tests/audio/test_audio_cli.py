import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from civiccast_brief.audio import cli
from civiccast_brief.audio.cli import build_parser, main
from civiccast_brief.audio.engines import RenderResult
from civiccast_brief.audio.errors import ConfigError, EngineError, FinishError
from civiccast_brief.audio.ffmpeg import tools_available

needs_ffmpeg = pytest.mark.skipif(not tools_available(), reason="ffmpeg/ffprobe not on PATH")
SCRIPT_TEXT = (
    "[CHAPTER Intro]\n"
    "[S1] Welcome to the council recap.\n"
    "[S2] Right, and staff said there were 32 candidates & more.\n"
    "[CHAPTER Votes]\n"
    "[S1] Ordinance 2026-69 passed five to two.\n"
)


def test_module_entry_point_prints_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "civiccast_brief.audio", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0
    assert "--engine" in proc.stdout and "chatterbox" in proc.stdout


def test_engine_defaults_to_dia2(tmp_path: Path) -> None:
    script = tmp_path / "s.txt"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    args = build_parser().parse_args([str(script), "--out", str(tmp_path / "o")])
    assert args.engine == "dia2"


def test_engine_choices_are_the_windows_adapters_and_gemini() -> None:
    parser = build_parser()
    engine = next(a for a in parser._actions if a.dest == "engine")
    assert list(engine.choices) == ["dia2", "chatterbox", "gemini", "fake"]


def test_stepaudio_is_not_an_engine_choice(tmp_path: Path, capsys) -> None:
    # Scott, 2026-11-12: Step-Audio-EditX is a Linux program and is not an option at all.
    script = tmp_path / "s.txt"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [str(script), "--engine", "stepaudio", "--out", str(tmp_path / "o")]
        )
    assert "invalid choice: 'stepaudio'" in capsys.readouterr().err


def test_bad_script_is_a_clean_error_not_a_traceback(tmp_path: Path, capsys) -> None:
    script = tmp_path / "bad.txt"
    script.write_text("A stray line with no speaker.\n", encoding="utf-8")
    code = main([str(script), "--engine", "fake", "--out", str(tmp_path / "out")])
    assert code == 1
    assert "error: line 1: expected" in capsys.readouterr().err
    assert not (tmp_path / "out" / "episode.mp3").exists()


def test_missing_intro_file_is_refused_before_any_work(tmp_path: Path, capsys) -> None:
    script = tmp_path / "s.txt"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    code = main(
        [
            str(script),
            "--engine",
            "fake",
            "--out",
            str(tmp_path / "out"),
            "--intro",
            str(tmp_path / "nope.mp3"),
        ]
    )
    assert code == 1
    assert "intro file not found" in capsys.readouterr().err


def test_bad_config_is_a_clean_error(tmp_path: Path, capsys) -> None:
    script = tmp_path / "s.txt"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    config = tmp_path / "cfg.json"
    config.write_text('{"box": {"hostname": "x"}}', encoding="utf-8")
    code = main(
        [
            str(script),
            "--engine",
            "chatterbox",
            "--out",
            str(tmp_path / "o"),
            "--config",
            str(config),
        ]
    )
    assert code == 1
    assert "unknown keys" in capsys.readouterr().err


@needs_ffmpeg
def test_fake_engine_run_writes_all_four_outputs(tmp_path: Path, capsys) -> None:
    script = tmp_path / "s.txt"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    out = tmp_path / "episode"
    code = main([str(script), "--engine", "fake", "--out", str(out)])
    assert code == 0
    for name in ("episode.mp3", "chapters.json", "transcript.vtt", "work/render.json"):
        assert (out / name).is_file(), name
    summary = capsys.readouterr().out
    assert "engine: fake (timing: exact)" in summary
    assert "chapters: 2" in summary and "(3 cues)" in summary


# --- --fallback-engine -------------------------------------------------------------------


class RecordingEngine:
    """A stand-in engine: records each render, and fails before writing when given an error."""

    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.renders: list[Path] = []

    def render(self, lines, out_wav: Path) -> RenderResult:
        self.renders.append(out_wav)
        if self.error is not None:
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            out_wav.write_bytes(b"partial")  # a half-written WAV the fallback must replace
            raise self.error
        return RenderResult(wav=out_wav, engine=self.name, timing="exact", lines=(), duration=1.0)


def _patch_engines(monkeypatch, engines) -> None:
    """Make ``make_engine`` build the stand-in engines; an exception value is raised instead."""

    def make_engine(name, config):
        value = engines[name]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cli, "make_engine", make_engine)


def _patch_finish(monkeypatch, error: Exception | None = None) -> None:
    def finish_episode(render, script, out, *, intro=None, outro=None):
        if error is not None:
            raise error
        return SimpleNamespace(
            loudness=SimpleNamespace(integrated_lufs=-16.0, true_peak_dbtp=-1.0),
            chapters={},
            chapters_json=out / "chapters.json",
            mp3=out / "episode.mp3",
            duration=1.0,
            transcript_vtt=out / "transcript.vtt",
            cues=[],
        )

    monkeypatch.setattr(cli, "finish_episode", finish_episode)


def _script(tmp_path: Path) -> Path:
    script = tmp_path / "s.txt"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    return script


def test_a_failed_engine_renders_with_the_fallback(tmp_path: Path, capsys, monkeypatch) -> None:
    primary = RecordingEngine("gemini", EngineError("daily quota spent"))
    fallback = RecordingEngine("dia2")
    _patch_engines(monkeypatch, {"gemini": primary, "dia2": fallback})
    _patch_finish(monkeypatch)
    out = tmp_path / "out"

    code = main(
        [
            str(_script(tmp_path)),
            "--engine",
            "gemini",
            "--fallback-engine",
            "dia2",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    assert primary.renders and fallback.renders
    captured = capsys.readouterr()
    assert "warning: gemini engine failed (daily quota spent); rendering with dia2" in captured.err
    assert "engine: dia2 (timing: exact)" in captured.out
    assert not (out / "work" / "voice.wav").exists(), "the partial WAV must be removed"


def test_a_config_error_making_the_engine_uses_the_fallback(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    fallback = RecordingEngine("dia2")
    _patch_engines(monkeypatch, {"gemini": ConfigError("no API key"), "dia2": fallback})
    _patch_finish(monkeypatch)

    code = main(
        [
            str(_script(tmp_path)),
            "--engine",
            "gemini",
            "--fallback-engine",
            "dia2",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 0
    assert fallback.renders
    err = capsys.readouterr().err
    assert "warning: gemini engine failed (no API key); rendering with dia2" in err


def test_both_engines_failing_exits_one_with_both_messages(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _patch_engines(
        monkeypatch,
        {"gemini": EngineError("gemini down"), "dia2": EngineError("box asleep")},
    )
    _patch_finish(monkeypatch)

    code = main(
        [
            str(_script(tmp_path)),
            "--engine",
            "gemini",
            "--fallback-engine",
            "dia2",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "error: gemini down" in err and "error: box asleep" in err


def test_no_fallback_engine_still_fails_as_before(tmp_path: Path, capsys, monkeypatch) -> None:
    primary = RecordingEngine("gemini", EngineError("gemini down"))
    _patch_engines(monkeypatch, {"gemini": primary, "dia2": RecordingEngine("dia2")})
    _patch_finish(monkeypatch)

    code = main([str(_script(tmp_path)), "--engine", "gemini", "--out", str(tmp_path / "out")])

    assert code == 1
    assert "error: gemini down" in capsys.readouterr().err


def test_a_finish_failure_does_not_trigger_the_fallback(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    primary = RecordingEngine("gemini")
    fallback = RecordingEngine("dia2")
    _patch_engines(monkeypatch, {"gemini": primary, "dia2": fallback})
    _patch_finish(monkeypatch, FinishError("loudness could not be measured"))

    code = main(
        [
            str(_script(tmp_path)),
            "--engine",
            "gemini",
            "--fallback-engine",
            "dia2",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 1
    assert primary.renders and not fallback.renders
    assert "error: loudness could not be measured" in capsys.readouterr().err
