import subprocess
import sys
from pathlib import Path

import pytest

from civiccast_brief.audio.cli import build_parser, main
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


def test_engine_has_no_default(tmp_path: Path) -> None:
    script = tmp_path / "s.txt"
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    with pytest.raises(SystemExit):
        build_parser().parse_args([str(script), "--out", str(tmp_path / "o")])


def test_engine_choices_are_the_three_adapters() -> None:
    parser = build_parser()
    engine = next(a for a in parser._actions if a.dest == "engine")
    assert list(engine.choices) == ["chatterbox", "dia2", "fake"]


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
