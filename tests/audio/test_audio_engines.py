import base64
import itertools
import json
import subprocess
from pathlib import Path

import pytest

from civiccast_brief.audio.engines import (
    AudioConfig,
    Box,
    ChatterboxConfig,
    ChatterboxRemote,
    Dia2Config,
    Dia2Remote,
    FakeEngine,
    RemoteConfig,
    chatterbox_commands,
    dia2_commands,
    dia2_spans,
    encode_powershell,
    fake_duration,
    fit_to_length,
    load_config,
    make_engine,
    parse_chatterbox_durations,
    parse_dia2_chunks,
    ps_quote,
    spans_from_durations,
    write_renderer_script,
)
from civiccast_brief.audio.errors import ConfigError, EngineError
from civiccast_brief.audio.ffmpeg import tools_available
from civiccast_brief.audio.lines import Line, parse_script

needs_ffmpeg = pytest.mark.skipif(not tools_available(), reason="ffmpeg/ffprobe not on PATH")
HOST = "nvideablackwell"
SCRIPT = parse_script(
    "[CHAPTER Intro]\n"
    "[S1] This is CivicCast Brief, for the October twentieth session.\n"
    "[S2] This episode is AI-generated from the public meeting video.\n"
    "[S1] Council approved the minutes.\n"
    "[CHAPTER Votes]\n"
    "[S2] Resolution 2026-75 passed six to one.\n"
)


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _decode_ps(argv) -> str:
    token = argv[-1].rsplit(" ", 1)[1]
    return base64.b64decode(token).decode("utf-16-le")


# --- quoting and transport -------------------------------------------------------------------


def test_ps_quote_doubles_single_quotes_and_keeps_backslashes() -> None:
    assert ps_quote(r"refs\carter_ref.wav") == "'refs\\carter_ref.wav'"
    assert ps_quote("it's") == "'it''s'"


def test_encoded_powershell_round_trips_through_utf16le() -> None:
    script = "Write-Output 'café'"
    assert base64.b64decode(encode_powershell(script)).decode("utf-16-le") == script


def test_box_run_sends_an_encoded_script_and_returns_stdout_on_success() -> None:
    seen = []

    def runner(argv, **kwargs):
        seen.append(argv)
        return _completed(argv, stdout="hello\nCIVICCAST_RC=0\n")

    box = Box(RemoteConfig(host=HOST), runner=runner)
    out = box.run(["Write-Output 'hello'"])
    assert out.startswith("hello")
    argv = seen[0]
    assert argv[0] == "ssh" and HOST in argv
    assert "-EncodedCommand" in argv[-1]
    script = _decode_ps(argv)
    assert "Write-Output 'hello'" in script
    assert "$ProgressPreference = 'SilentlyContinue'" in script
    assert "Write-Output ('CIVICCAST_RC=' + $rc)" in script


def test_box_run_reports_a_nonzero_remote_exit_code() -> None:
    def runner(argv, **kwargs):
        return _completed(argv, stdout="boom\nCIVICCAST_RC=3\n")

    with pytest.raises(EngineError, match="exited 3"):
        Box(RemoteConfig(host=HOST), runner=runner).run(["x"])


def test_box_run_refuses_output_without_an_exit_marker() -> None:
    def runner(argv, **kwargs):
        return _completed(argv, stdout="partial output and no marker\n")

    with pytest.raises(EngineError, match="no exit marker"):
        Box(RemoteConfig(host=HOST), runner=runner).run(["x"])


def test_box_run_reports_an_ssh_connection_failure() -> None:
    def runner(argv, **kwargs):
        return _completed(argv, returncode=255, stderr="Connection refused")

    with pytest.raises(EngineError, match="ssh to nvideablackwell failed"):
        Box(RemoteConfig(host=HOST), runner=runner).run(["x"])


def test_box_copies_use_forward_slashes_and_the_right_direction(tmp_path: Path) -> None:
    seen = []

    def runner(argv, **kwargs):
        seen.append(argv)
        return _completed(argv)

    box = Box(RemoteConfig(host=HOST), runner=runner)
    local = tmp_path / "script.txt"
    box.upload(local, r"C:\dev\run\script.txt")
    box.download(r"C:\dev\run\voice.wav", tmp_path / "voice.wav")
    up, down = seen
    assert up[-2] == str(local) and up[-1] == f"{HOST}:C:/dev/run/script.txt"
    assert down[-2] == f"{HOST}:C:/dev/run/voice.wav" and down[-1].endswith("voice.wav")


def test_box_copy_failure_is_an_engine_error(tmp_path: Path) -> None:
    def runner(argv, **kwargs):
        return _completed(argv, returncode=1, stderr="no such file")

    with pytest.raises(EngineError, match="no such file"):
        Box(RemoteConfig(host=HOST), runner=runner).download(r"C:\x.wav", tmp_path / "x.wav")


def test_renderer_script_carries_only_spoken_lines_as_utf8_lf(tmp_path: Path) -> None:
    path = tmp_path / "script.txt"
    write_renderer_script(list(SCRIPT.lines), path)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert "CHAPTER" not in text
    assert text.splitlines()[0].startswith("[S1] This is CivicCast Brief")
    assert len(text.splitlines()) == len(SCRIPT.lines)


# --- remote command builders -----------------------------------------------------------------


def test_chatterbox_command_passes_voices_in_speaker_order_from_config() -> None:
    cfg = ChatterboxConfig()
    commands = chatterbox_commands(cfg, r"C:\run\script.txt", r"C:\run\voice.wav")
    call = commands[-1]
    assert call.startswith("& ")
    assert call.index("'builtin'") < call.index("'refs\\carter_ref.wav'")
    assert "'C:\\run\\script.txt' 'C:\\run\\voice.wav'" in call
    assert any(c.startswith("$env:HF_HOME = ") for c in commands)
    assert any(c.startswith("Set-Location -LiteralPath") for c in commands)


def test_chatterbox_command_leaves_hf_home_alone_when_unset() -> None:
    commands = chatterbox_commands(ChatterboxConfig(hf_home=""), "s", "w")
    assert not any("HF_HOME" in c for c in commands)


def test_dia2_command_carries_the_bake_off_settings() -> None:
    call = dia2_commands(Dia2Config(), r"C:\run\script.txt", r"C:\run\voice.wav")[-1]
    for fragment in (
        "'--lines-per-chunk' '2'",
        "'--cfg' '3.0'",
        "'--temp' '0.6'",
        "'--gap' '0.3'",
        "'--atempo' '0.85'",
        "'--ref1' 'C:\\dev\\civiccast-brief-bakeoff\\dia2tune\\ref_s1.wav'",
    ):
        assert fragment in call


# --- timing ----------------------------------------------------------------------------------


def _stdout_for(durations, speakers) -> str:
    rows = [
        f"line {i:02d} {spk} audio={dur:6.2f}s gen=  1.00s"
        for i, (dur, spk) in enumerate(zip(durations, speakers, strict=True), 1)
    ]
    return "\n".join([*rows, "CIVICCAST_RC=0"]) + "\n"


def test_chatterbox_durations_parse_in_order_and_must_match_the_script() -> None:
    lines = list(SCRIPT.lines)  # four spoken lines; the two chapter marks are not lines
    stdout = _stdout_for([2.0, 3.5, 1.25, 4.0], [line.speaker for line in lines])
    assert parse_chatterbox_durations(stdout, lines) == [2.0, 3.5, 1.25, 4.0]
    with pytest.raises(EngineError, match="reported 3 lines, expected 4"):
        parse_chatterbox_durations(_stdout_for([1.0] * 3, ["S1"] * 3), lines)
    with pytest.raises(EngineError, match="does not match"):
        parse_chatterbox_durations(_stdout_for([1.0] * 4, ["S2"] * 4), lines)


def test_spans_from_durations_insert_the_gap_between_lines_only() -> None:
    spans = spans_from_durations([2.0, 1.0], 0.35)
    assert spans == [(0.0, 2.0), (2.35, 3.35)]


def test_fit_to_length_ends_exactly_at_the_real_file_length() -> None:
    spans = spans_from_durations([2.0, 3.0, 1.0], 0.35)
    fitted = fit_to_length(spans, 7.0)
    assert fitted[-1][1] == pytest.approx(7.0)
    assert fitted[0][0] == 0.0
    for (_, end), (start, _) in itertools.pairwise(fitted):
        assert start > end  # gaps survive the stretch


def test_dia2_chunk_summary_is_checked_for_contiguity_and_line_count() -> None:
    summary = {
        "chunks": [
            {"first_line": 1, "lines": 2, "audio_s": 12.88},
            {"first_line": 3, "lines": 2, "audio_s": 13.2},
        ]
    }
    assert parse_dia2_chunks(summary, 4) == [(1, 2, 12.88), (3, 2, 13.2)]
    with pytest.raises(EngineError, match="covers 4 lines, script has 5"):
        parse_dia2_chunks(summary, 5)
    gap = {
        "chunks": [
            {"first_line": 1, "lines": 1, "audio_s": 1.0},
            {"first_line": 3, "lines": 1, "audio_s": 1.0},
        ]
    }
    with pytest.raises(EngineError, match="not contiguous"):
        parse_dia2_chunks(gap, 2)


def test_dia2_spans_put_chunk_edges_exactly_and_split_inside_by_words() -> None:
    lines = [
        Line(1, "S1", "one two three"),  # 3 words
        Line(2, "S2", "four"),  # 1 word
        Line(3, "S1", "five"),
        Line(4, "S2", "six"),
    ]
    spans = dia2_spans([(1, 2, 8.0), (3, 2, 4.0)], lines, 0.3)
    assert spans[0] == (0.0, pytest.approx(6.0))  # 8 s split 3:1
    assert spans[1] == (pytest.approx(6.0), pytest.approx(8.0))
    assert spans[2][0] == pytest.approx(8.3)  # chunk 2 starts after the 0.3 s gap
    assert spans[3][1] == pytest.approx(8.3 + 4.0)


def test_fake_duration_is_predictable_and_has_a_floor() -> None:
    assert fake_duration("hi") == 0.5
    assert fake_duration("one two three four five six seven eight nine ten") == pytest.approx(3.0)


# --- config ----------------------------------------------------------------------------------


def test_default_config_points_at_the_bake_off_box() -> None:
    config = AudioConfig()
    assert config.box.host == "nvideablackwell"
    assert config.chatterbox.voice_s1 == "builtin"
    assert config.chatterbox.voice_s2 == r"refs\carter_ref.wav"
    assert config.dia2.ref_s2.endswith(r"dia2tune\ref_s2.wav")


def test_config_file_overrides_nested_values(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"box": {"host": "other-box"}, "chatterbox": {"voice_s2": "refs\\x.wav"}}),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.box.host == "other-box"
    assert config.box.scratch_root == RemoteConfig().scratch_root
    assert config.chatterbox.voice_s2 == "refs\\x.wav"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"box": {"hots": "typo"}}, "unknown keys"),
        ({"box": {"timeout_s": "soon"}}, "expected float"),
        ({"dia2": {"lines_per_chunk": 2.5}}, "expected int"),
        ({"dia2": 3}, "must be a JSON object"),
    ],
)
def test_bad_config_is_refused_with_the_key_named(
    tmp_path: Path, payload: dict, message: str
) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_unreadable_config_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(tmp_path / "missing.json")


def test_make_engine_knows_the_three_engines_and_refuses_others() -> None:
    config = AudioConfig()
    assert isinstance(make_engine("fake", config), FakeEngine)
    assert isinstance(make_engine("chatterbox", config), ChatterboxRemote)
    assert isinstance(make_engine("dia2", config), Dia2Remote)
    with pytest.raises(ConfigError, match="unknown engine"):
        make_engine("kokoro", config)


# --- remote orchestration with a stub runner (no ssh, no GPU) --------------------------------


class StubBox:
    """Stands in for ssh and scp. Downloads return a fixture WAV, or a JSON summary."""

    def __init__(self, fixture: Path, stdout: str = "", summary: dict | None = None) -> None:
        self.fixture = fixture
        self.stdout = stdout
        self.summary = summary
        self.scripts: list[str] = []
        self.uploads: dict[str, bytes] = {}

    def __call__(self, argv, **kwargs):
        if argv[0] == "ssh":
            script = _decode_ps(argv)
            self.scripts.append(script)
            if "New-Item" in script:
                return _completed(argv, stdout="CIVICCAST_RC=0\n")
            return _completed(argv, stdout=self._stdout(script))
        remote = next(arg for arg in argv if arg.startswith(f"{HOST}:"))
        if remote == argv[-2]:  # download: remote is the source
            local = Path(argv[-1])
            local.parent.mkdir(parents=True, exist_ok=True)
            if remote.endswith(".json"):
                local.write_text(json.dumps(self.summary), encoding="utf-8")
            else:
                local.write_bytes(self.fixture.read_bytes())
        else:  # upload: local is the source
            self.uploads[remote] = Path(argv[-2]).read_bytes()
        return _completed(argv)

    def _stdout(self, script: str) -> str:
        if "render_turbo2" in script:
            count = sum(1 for line in self.uploaded_script().splitlines() if line.strip())
            spk = [line[1:3] for line in self.uploaded_script().splitlines()]
            return _stdout_for([1.0] * count, spk)
        return self.stdout + "CIVICCAST_RC=0\n"

    def uploaded_script(self) -> str:
        return next(v for k, v in self.uploads.items() if k.endswith("script.txt")).decode("utf-8")


@needs_ffmpeg
def test_chatterbox_remote_render_uses_measured_timing_and_the_bake_off_command(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture.wav"
    FakeEngine().render(list(SCRIPT.lines), fixture)
    stub = StubBox(fixture)
    engine = ChatterboxRemote(RemoteConfig(host=HOST), ChatterboxConfig(), runner=stub)
    result = engine.render(list(SCRIPT.lines), tmp_path / "out" / "voice.wav")

    assert result.timing == "measured" and result.engine == "chatterbox"
    assert [line.number for line in result.lines] == [2, 3, 4, 6]
    assert [line.speaker for line in result.lines] == ["S1", "S2", "S1", "S2"]
    assert result.lines[-1].end == pytest.approx(result.duration, abs=0.002)
    assert (tmp_path / "out" / "voice.wav").is_file()
    render_call = next(s for s in stub.scripts if "render_turbo2" in s)
    assert "'builtin' 'refs\\carter_ref.wav'" in render_call
    assert "CHAPTER" not in stub.uploaded_script()


@needs_ffmpeg
def test_dia2_remote_render_downloads_the_tempo_copy_and_splits_chunks(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.wav"
    FakeEngine().render(list(SCRIPT.lines), fixture)
    summary = {
        "chunks": [
            {"first_line": 1, "lines": 2, "audio_s": 3.0},
            {"first_line": 3, "lines": 2, "audio_s": 2.0},
        ]
    }
    stub = StubBox(fixture, summary=summary)
    engine = Dia2Remote(RemoteConfig(host=HOST), Dia2Config(), runner=stub)
    result = engine.render(list(SCRIPT.lines), tmp_path / "out" / "voice.wav")

    assert result.timing == "approximate" and result.engine == "dia2"
    assert [line.number for line in result.lines] == [2, 3, 4, 6]
    for earlier, later in itertools.pairwise(result.lines):
        assert earlier.end <= later.start
    # The second chunk starts after the first chunk's 3.0 s and the 0.3 s gap, before stretching.
    assert result.lines[2].start > result.lines[1].end
    assert result.lines[-1].end == pytest.approx(result.duration, abs=0.002)
    dia2_call = next(s for s in stub.scripts if "render_dia2_chunked" in s)
    assert "'--atempo' '0.85'" in dia2_call
    # The renderer's tempo copy is the file that comes back, not the raw --out file.
    assert (tmp_path / "out" / "voice.wav").is_file()
