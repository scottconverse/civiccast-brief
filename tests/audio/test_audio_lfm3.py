"""LFM3 Audio (local llama-tts): argv, joining, exe lookup and errors. No model, no real tool."""

import dataclasses
import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from civiccast_brief.audio import cli
from civiccast_brief.audio.engines import (
    LFM3_MODEL,
    LFM3_WINGET_EXE,
    AudioConfig,
    LfmConfig,
    LfmLocal,
    RenderResult,
    lfm3_executable,
    load_config,
    make_engine,
)
from civiccast_brief.audio.errors import ConfigError, EngineError
from civiccast_brief.audio.lines import Line

RATE = 24000


def _pcm(seconds: float, value: int = 0, rate: int = RATE) -> bytes:
    return value.to_bytes(2, "little", signed=True) * round(seconds * rate)


def _write_wav(
    path: Path, seconds: float, value: int = 0, rate: int = RATE, channels: int = 1
) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(_pcm(seconds, value, rate))


class FakeLlama:
    """A llama-tts stand-in: writes a real WAV per call, with a distinct sample per line."""

    def __init__(
        self,
        durations: list[float],
        *,
        bad_call: int | None = None,
        bad_channels: int | None = None,
        bad_rate: int | None = None,
        **failure,
    ) -> None:
        self.durations = durations
        self.bad_call = bad_call
        self.bad_channels = bad_channels
        self.bad_rate = bad_rate
        self.failure = failure
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if "timeout" in self.failure:
            raise subprocess.TimeoutExpired(argv, self.failure["timeout"])
        if "returncode" in self.failure:
            return subprocess.CompletedProcess(
                argv, self.failure["returncode"], "", self.failure.get("stderr", "")
            )
        out = Path(argv[argv.index("-o") + 1])
        if not self.failure.get("empty"):
            channels = 1
            rate = RATE
            if self.bad_call == len(self.calls):
                if self.bad_channels is not None:
                    channels = self.bad_channels
                if self.bad_rate is not None:
                    rate = self.bad_rate
            _write_wav(
                out,
                self.durations[len(self.calls) - 1],
                1000 + len(self.calls),
                rate=rate,
                channels=channels,
            )
        return subprocess.CompletedProcess(argv, 0, "", "")


def _config(tmp_path: Path, **overrides) -> LfmConfig:
    exe = tmp_path / "llama-tts.exe"
    exe.write_bytes(b"")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"")
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"")
    values = {"llama_tts": str(exe), "model": str(model), "mmproj": str(mmproj)}
    values.update(overrides)
    return LfmConfig(**values)


def _lines() -> list[Line]:
    return [
        Line(1, "S1", "Welcome to the council recap."),
        Line(2, "S2", "Right, and staff said there were 32 candidates."),
        Line(3, "S1", "Ordinance 2026-69 passed five to two."),
    ]


def _temp_dirs(parent: Path) -> list[Path]:
    return [path for path in parent.iterdir() if path.name.startswith("civiccast-lfm3-")]


# --- argv ------------------------------------------------------------------------------------


def test_each_line_is_rendered_with_its_model_mmproj_text_voice_and_output(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runner = FakeLlama([0.4, 0.5, 0.6])
    LfmLocal(cfg, runner=runner).render(_lines(), tmp_path / "out" / "voice.wav")

    assert len(runner.calls) == 3
    for argv, line, voice in zip(runner.calls, _lines(), ["male", "female", "male"], strict=True):
        assert argv[0] == cfg.llama_tts
        assert argv[argv.index("-m") + 1] == cfg.model
        assert argv[argv.index("-mm") + 1] == cfg.mmproj
        assert argv[argv.index("-p") + 1] == line.text
        assert argv[argv.index("--tts-voice") + 1] == voice
        assert argv[argv.index("-o") + 1].endswith(".wav")


def test_configured_voices_are_used_for_their_speaker(tmp_path: Path) -> None:
    cfg = _config(tmp_path, voice_s1="female", voice_s2="male")
    runner = FakeLlama([0.3, 0.3])
    LfmLocal(cfg, runner=runner).render(_lines()[:2], tmp_path / "voice.wav")
    assert [argv[argv.index("--tts-voice") + 1] for argv in runner.calls] == ["female", "male"]


# --- joining and timings ---------------------------------------------------------------------


def test_lines_are_joined_in_order_with_the_gap_and_measured_timing(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    out = tmp_path / "out" / "voice.wav"
    result = LfmLocal(cfg, runner=FakeLlama([0.4, 0.6, 0.2])).render(_lines(), out)

    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == RATE
        frames = wav.readframes(wav.getnframes())
    values = [
        int.from_bytes(frames[i : i + 2], "little", signed=True) for i in range(0, len(frames), 2)
    ]
    order = [
        value for index, value in enumerate(values) if index == 0 or value != values[index - 1]
    ]
    assert [value for value in order if value != 0] == [1001, 1002, 1003]

    assert result.timing == "measured"
    assert result.duration == pytest.approx(0.4 + 0.35 + 0.6 + 0.35 + 0.2, abs=0.02)
    assert [line.number for line in result.lines] == [1, 2, 3]
    spans = [(line.start, line.end) for line in result.lines]
    assert spans[0][0] == 0.0
    assert spans[1][0] - spans[0][1] == pytest.approx(0.35, abs=0.02)
    assert spans[2][0] - spans[1][1] == pytest.approx(0.35, abs=0.02)
    assert spans[-1][1] == pytest.approx(result.duration, abs=0.02)
    assert _temp_dirs(out.parent) == []


def test_a_300_line_render_joins_every_line_in_python(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    lines = [
        Line(index + 1, "S1" if index % 2 == 0 else "S2", f"Line {index + 1} text.")
        for index in range(300)
    ]
    out = tmp_path / "out" / "voice.wav"
    result = LfmLocal(cfg, runner=FakeLlama([0.01] * 300)).render(lines, out)

    assert len(result.lines) == 300
    assert [line.number for line in result.lines] == list(range(1, 301))
    expected = 300 * 0.01 + 299 * cfg.gap_s
    assert result.duration == pytest.approx(expected, abs=0.001)
    with wave.open(str(out), "rb") as wav:
        assert wav.getnframes() == 300 * 240 + 299 * 8400
    assert _temp_dirs(out.parent) == []


@pytest.mark.parametrize(
    ("channels", "rate"), [(2, RATE), (1, 16000)], ids=["stereo", "wrong-rate"]
)
def test_a_clip_with_the_wrong_format_names_the_line_and_removes_the_temp_folder(
    tmp_path: Path, channels: int, rate: int
) -> None:
    cfg = _config(tmp_path)
    runner = FakeLlama([0.3, 0.3, 0.3], bad_call=2, bad_channels=channels, bad_rate=rate)
    out = tmp_path / "out" / "voice.wav"

    with pytest.raises(EngineError, match=r"line 2:") as caught:
        LfmLocal(cfg, runner=runner).render(_lines(), out)

    message = str(caught.value)
    if channels != 1:
        assert "2 channel(s)" in message and "mono 16-bit" in message
    else:
        assert "16000 Hz" in message and f"expected {RATE} Hz" in message
    assert not out.exists()
    assert _temp_dirs(out.parent) == []


# --- executable lookup -----------------------------------------------------------------------


def test_executable_is_the_configured_path_first() -> None:
    cfg = LfmConfig(llama_tts=r"C:\tools\llama-tts.exe")
    assert lfm3_executable(cfg, lambda name: r"C:\path\llama-tts.exe") == cfg.llama_tts


def test_executable_falls_back_to_path() -> None:
    found = lfm3_executable(LfmConfig(), lambda name: r"C:\path\llama-tts.exe", {})
    assert found == r"C:\path\llama-tts.exe"


def test_executable_falls_back_to_the_winget_package_path() -> None:
    found = lfm3_executable(
        LfmConfig(), lambda name: None, {"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}
    )
    assert found == str(Path(r"C:\Users\me\AppData\Local") / LFM3_WINGET_EXE)


# --- missing pieces --------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["exe", "model", "mmproj"])
def test_a_missing_exe_model_or_mmproj_is_a_config_error_naming_it(
    tmp_path: Path, missing: str
) -> None:
    cfg = _config(tmp_path)
    field, value = {
        "exe": ("llama_tts", str(tmp_path / "nope.exe")),
        "model": ("model", str(tmp_path / "nope.gguf")),
        "mmproj": ("mmproj", str(tmp_path / "nope-mmproj.gguf")),
    }[missing]
    cfg = dataclasses.replace(cfg, **{field: value})
    runner = FakeLlama([0.3, 0.3, 0.3])

    with pytest.raises(ConfigError, match="not found") as caught:
        LfmLocal(cfg, runner=runner).render(_lines(), tmp_path / "out" / "voice.wav")

    assert value in str(caught.value)
    assert runner.calls == []
    assert not (tmp_path / "out" / "voice.wav").exists()


# --- failures with a line number -------------------------------------------------------------


def test_a_non_zero_exit_names_the_line_and_removes_the_temp_folder(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runner = FakeLlama([0.3], returncode=2, stderr="model failed to load\n")
    out = tmp_path / "out" / "voice.wav"

    with pytest.raises(EngineError, match=r"line 1: llama-tts exited 2") as caught:
        LfmLocal(cfg, runner=runner).render(_lines(), out)

    assert "model failed to load" in str(caught.value)
    assert _temp_dirs(out.parent) == []


def test_a_timeout_names_the_line_and_removes_the_temp_folder(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runner = FakeLlama([0.3, 0.3], timeout=300.0)
    out = tmp_path / "out" / "voice.wav"

    with pytest.raises(EngineError, match=r"line 1: llama-tts ran past 300 s"):
        LfmLocal(cfg, runner=runner).render(_lines(), out)

    assert _temp_dirs(out.parent) == []


def test_an_empty_wav_names_the_line_and_removes_the_temp_folder(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runner = FakeLlama([0.3, 0.3], empty=True)
    out = tmp_path / "out" / "voice.wav"

    with pytest.raises(EngineError, match=r"line 1: llama-tts wrote no audio"):
        LfmLocal(cfg, runner=runner).render(_lines(), out)

    assert _temp_dirs(out.parent) == []


def test_an_empty_script_is_an_engine_error(tmp_path: Path) -> None:
    runner = FakeLlama([])
    with pytest.raises(EngineError, match="no spoken lines"):
        LfmLocal(_config(tmp_path), runner=runner).render([], tmp_path / "voice.wav")
    assert runner.calls == []


# --- wiring ----------------------------------------------------------------------------------


def test_make_engine_builds_lfm3() -> None:
    engine = make_engine("lfm3", AudioConfig())
    assert isinstance(engine, LfmLocal)
    assert engine.name == "lfm3"
    assert LFM3_MODEL.endswith(".gguf")


def test_config_json_overrides_the_lfm3_voice(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"lfm3": {"voice_s1": "female"}}), encoding="utf-8")
    config = load_config(path)
    assert config.lfm3.voice_s1 == "female"
    assert config.lfm3.voice_s2 == AudioConfig().lfm3.voice_s2  # the other voice is untouched
    assert config.lfm3.gap_s == 0.35


# --- CLI fallback ----------------------------------------------------------------------------


class RecordingEngine:
    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.renders: list[Path] = []

    def render(self, lines, out_wav: Path) -> RenderResult:
        self.renders.append(out_wav)
        if self.error is not None:
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            out_wav.write_bytes(b"partial")
            raise self.error
        return RenderResult(
            wav=out_wav, engine=self.name, timing="measured", lines=(), duration=1.0
        )


def test_cli_uses_lfm3_when_the_gemini_render_fails(tmp_path: Path, capsys, monkeypatch) -> None:
    primary = RecordingEngine("gemini", EngineError("daily quota spent"))
    fallback = RecordingEngine("lfm3")
    monkeypatch.setattr(
        cli,
        "make_engine",
        lambda name, config: {"gemini": primary, "lfm3": fallback}[name],
    )

    def finish_episode(render, script, out, *, intro=None, outro=None):
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
    script = tmp_path / "s.txt"
    script.write_text("[S1] Welcome.\n[S2] Thanks.\n", encoding="utf-8")

    code = cli.main(
        [
            str(script),
            "--engine",
            "gemini",
            "--fallback-engine",
            "lfm3",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 0
    assert primary.renders and fallback.renders
    assert "rendering with lfm3" in capsys.readouterr().err
