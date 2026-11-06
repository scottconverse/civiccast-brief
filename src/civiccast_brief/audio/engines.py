"""Voice engines behind one adapter interface.

An engine takes the spoken lines of a script and writes one WAV. It also reports where each line
starts and ends in that WAV, and how exact those times are. Chapter marks never reach an engine.

Timing, per engine:

- ``exact`` (FakeEngine): the engine writes each line itself, so the times are known.
- ``measured`` (ChatterboxRemote): the remote renderer prints the audio length of every line and
  puts a fixed 0.35 s gap between lines. Start times are the running sum of lengths and gaps, then
  the timeline is stretched to the length of the file that came back. The gaps and the total are
  exact. A line's end includes the quiet tail the TTS model leaves at the end of that line. On the
  90-second e2e render, silence detection put the audible end 0.13 to 0.34 s before each line's
  end, and the audible start of the next line 0.02 to 0.11 s after the next start. The offset did
  not grow across the render. Captions can therefore run a few tenths of a second past the speech.
- ``approximate`` (Dia2Remote): the renderer reports the exact length of each two-line chunk but
  not where one line ends inside a chunk. That boundary is estimated from the word counts of the
  two lines, then the timeline is stretched to the file length. Chunk edges use the renderer's
  measured chunk lengths and carry the same quiet-tail offset as ``measured``. A boundary inside a
  chunk is an estimate and was not checked against the audio. Dia2 has no e2e render here.

The remote engines drive the bake-off renderers on the GPU box over ssh and scp. Remote commands
are PowerShell (the box's default shell) sent with -EncodedCommand, so nothing has to survive
cmd.exe quoting. The outer PowerShell reports exit code 1 whenever the inner command failed, so
the exit code is not trusted. Each remote command prints CIVICCAST_RC=<n>, and that line decides.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import re
import subprocess
import tempfile
import time
import typing
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from civiccast_brief.audio.errors import ConfigError, EngineError
from civiccast_brief.audio.ffmpeg import probe_duration, run_ffmpeg
from civiccast_brief.audio.lines import Line

Timing = Literal["exact", "measured", "approximate"]

SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20")
RC_MARKER = "CIVICCAST_RC="
ENGINE_NAMES = ("chatterbox", "dia2", "fake")

_CHATTER_LINE = re.compile(r"^line\s+(\d+)\s+(S[12])\s+audio=\s*([0-9.]+)s", re.MULTILINE)
_RC_LINE = re.compile(r"^CIVICCAST_RC=(-?\d+)\s*$", re.MULTILINE)


# --- results ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineTiming:
    number: int  # source line number in the script
    speaker: str
    start: float  # seconds from the start of the rendered WAV
    end: float


@dataclass(frozen=True)
class RenderResult:
    wav: Path
    engine: str
    timing: Timing
    lines: tuple[LineTiming, ...]
    duration: float  # seconds, measured from the WAV with ffprobe


class VoiceEngine(Protocol):
    name: str

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult: ...


# --- configuration ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteConfig:
    """The GPU box. Paths in the engine configs are Windows paths on that box."""

    host: str = "nvideablackwell"
    scratch_root: str = r"C:\dev\civiccast-brief-bakeoff\out\phase3"
    ssh: str = "ssh"
    scp: str = "scp"
    timeout_s: float = 3600.0


@dataclass(frozen=True)
class ChatterboxConfig:
    python: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox\venv\Scripts\python.exe"
    renderer: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox\render_turbo2.py"
    workdir: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox"
    hf_home: str = r"C:\dev\civiccast-brief-bakeoff\chatterbox\hf-home"
    # Voice per speaker: "builtin" or a WAV path relative to workdir. S1 is Maya, S2 is Theo.
    voice_s1: str = "builtin"
    voice_s2: str = r"refs\carter_ref.wav"
    # Must equal GAP_S in render_turbo2.py, which is not a parameter there.
    gap_s: float = 0.35


@dataclass(frozen=True)
class Dia2Config:
    python: str = r"C:\dev\civiccast-brief-bakeoff\dia2\.venv\Scripts\python.exe"
    renderer: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune\render_dia2_chunked.py"
    workdir: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune"
    hf_home: str = ""  # empty leaves the environment alone
    ref_s1: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune\ref_s1.wav"
    ref_s2: str = r"C:\dev\civiccast-brief-bakeoff\dia2tune\ref_s2.wav"
    # Settings picked in the bake-off (see docs/voice-bakeoff.md, round 2, variant C).
    lines_per_chunk: int = 2
    cfg: float = 3.0
    temp: float = 0.6
    gap_s: float = 0.3
    atempo: float = 0.85


@dataclass(frozen=True)
class AudioConfig:
    box: RemoteConfig = field(default_factory=RemoteConfig)
    chatterbox: ChatterboxConfig = field(default_factory=ChatterboxConfig)
    dia2: Dia2Config = field(default_factory=Dia2Config)


def load_config(path: Path | None) -> AudioConfig:
    """Defaults, or a JSON file whose keys override them. Unknown keys are an error."""
    if path is None:
        return AudioConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    return _build(AudioConfig, data, "config")


def _build(cls: type, data: object, where: str) -> typing.Any:
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a JSON object")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {unknown}")
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for name, value in data.items():
        hint = hints[name]
        if dataclasses.is_dataclass(hint):
            kwargs[name] = _build(hint, value, f"{where}.{name}")
        else:
            kwargs[name] = _coerce(hint, value, f"{where}.{name}")
    return cls(**kwargs)


def _coerce(hint: type, value: object, where: str) -> object:
    """Accept a JSON value for a str, int or float field. Anything else is an error."""
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    ok = (
        (hint is str and isinstance(value, str))
        or (hint is int and isinstance(value, int) and not isinstance(value, bool))
        or (hint is float and is_number)
    )
    if not ok:
        raise ConfigError(f"{where}: expected {hint.__name__}, got {value!r}")
    return hint(value)


def make_engine(name: str, config: AudioConfig) -> VoiceEngine:
    if name == "chatterbox":
        return ChatterboxRemote(config.box, config.chatterbox)
    if name == "dia2":
        return Dia2Remote(config.box, config.dia2)
    if name == "fake":
        return FakeEngine()
    raise ConfigError(f"unknown engine {name!r}; choose from {', '.join(ENGINE_NAMES)}")


# --- PowerShell and transport helpers --------------------------------------------------------

Runner = Callable[..., subprocess.CompletedProcess]


def ps_quote(value: str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def win_join(*parts: str) -> str:
    return "\\".join(part.rstrip("\\") for part in parts)


def _invoke(argv: list[str]) -> str:
    return "& " + " ".join(ps_quote(arg) for arg in argv)


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _scp_path(remote: str) -> str:
    return remote.replace("\\", "/")


def encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


class Box:
    """Runs PowerShell on the GPU box and copies files to and from it."""

    def __init__(self, cfg: RemoteConfig, runner: Runner = subprocess.run) -> None:
        self.cfg = cfg
        self._runner = runner

    def run(self, commands: list[str]) -> str:
        """Run PowerShell lines on the box. Returns stdout, or raises EngineError."""
        script = "\n".join(
            [
                "$ProgressPreference = 'SilentlyContinue'",
                *commands,
                "$rc = $LASTEXITCODE",
                "if ($null -eq $rc) { $rc = 0 }",
                f"Write-Output ('{RC_MARKER}' + $rc)",
            ]
        )
        argv = [
            self.cfg.ssh,
            *SSH_OPTIONS,
            self.cfg.host,
            "powershell -NoProfile -NonInteractive -EncodedCommand " + encode_powershell(script),
        ]
        proc = self._call(argv, f"command on {self.cfg.host}")
        if proc.returncode == 255:
            raise EngineError(f"ssh to {self.cfg.host} failed:\n{_tail(proc.stderr)}")
        match = _RC_LINE.search(proc.stdout)
        if match is None:
            raise EngineError(
                f"remote command on {self.cfg.host} gave no exit marker; output tail:\n"
                f"{_tail(proc.stdout)}\n{_tail(proc.stderr)}"
            )
        code = int(match.group(1))
        if code != 0:
            raise EngineError(
                f"remote command exited {code}; output tail:\n{_tail(proc.stdout)}\n"
                f"{_tail(proc.stderr)}"
            )
        return proc.stdout

    def mkdir(self, remote_dir: str) -> None:
        self.run(
            [
                f"New-Item -ItemType Directory -Force -ErrorAction Stop -Path {ps_quote(remote_dir)} | Out-Null"
            ]
        )

    def new_run_dir(self) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return win_join(self.cfg.scratch_root, f"{stamp}-{uuid.uuid4().hex[:6]}")

    def upload(self, local: Path, remote: str) -> None:
        argv = [self.cfg.scp, *SSH_OPTIONS, str(local), f"{self.cfg.host}:{_scp_path(remote)}"]
        self._copy(argv, f"upload {local.name}")

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.cfg.scp, *SSH_OPTIONS, f"{self.cfg.host}:{_scp_path(remote)}", str(local)]
        self._copy(argv, f"download {remote}")

    def _copy(self, argv: list[str], what: str) -> None:
        proc = self._call(argv, what)
        if proc.returncode != 0:
            raise EngineError(f"scp {what} failed (exit {proc.returncode}):\n{_tail(proc.stderr)}")

    def _call(self, argv: list[str], what: str) -> subprocess.CompletedProcess:
        try:
            return self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.cfg.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"{what} ran past {self.cfg.timeout_s:.0f} s") from exc
        except FileNotFoundError as exc:
            raise EngineError(f"{argv[0]} is not on PATH") from exc


def write_renderer_script(lines: list[Line], path: Path) -> None:
    """Write the spoken lines as `[S1] text` lines. Chapter marks are never written."""
    text = "".join(f"[{line.speaker}] {line.text}\n" for line in lines)
    path.write_text(text, encoding="utf-8", newline="\n")


# --- timing helpers --------------------------------------------------------------------------


def spans_from_durations(durations: list[float], gap_s: float) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for duration in durations:
        spans.append((cursor, cursor + duration))
        cursor += duration + gap_s
    return spans


def fit_to_length(spans: list[tuple[float, float]], actual: float) -> list[tuple[float, float]]:
    """Stretch a timeline so it ends where the real file ends. Removes small estimate errors."""
    raw_total = spans[-1][1]
    if raw_total <= 0 or actual <= 0:
        raise EngineError("cannot fit timings to an empty render")
    scale = actual / raw_total
    return [(start * scale, end * scale) for start, end in spans]


def parse_chatterbox_durations(stdout: str, lines: list[Line]) -> list[float]:
    """Per-line audio lengths printed by render_turbo2.py, checked against the lines sent."""
    found = _CHATTER_LINE.findall(stdout)
    if len(found) != len(lines):
        raise EngineError(
            f"renderer reported {len(found)} lines, expected {len(lines)}; output tail:\n"
            f"{_tail(stdout)}"
        )
    durations: list[float] = []
    for index, (number, speaker, seconds) in enumerate(found):
        if int(number) != index + 1 or speaker != lines[index].speaker:
            raise EngineError(f"renderer line {number} ({speaker}) does not match the script")
        durations.append(float(seconds))
    return durations


def parse_dia2_chunks(data: object, line_count: int) -> list[tuple[int, int, float]]:
    """(first_line, line_count, audio_s) per chunk from the renderer's JSON summary."""
    chunks = data.get("chunks") if isinstance(data, dict) else None
    if not isinstance(chunks, list) or not chunks:
        raise EngineError("Dia2 summary has no chunks")
    result: list[tuple[int, int, float]] = []
    expected_first = 1
    for chunk in chunks:
        first, count, audio = int(chunk["first_line"]), int(chunk["lines"]), float(chunk["audio_s"])
        if first != expected_first or count < 1:
            raise EngineError(f"Dia2 chunks are not contiguous at line {expected_first}")
        result.append((first, count, audio))
        expected_first += count
    if expected_first - 1 != line_count:
        raise EngineError(
            f"Dia2 summary covers {expected_first - 1} lines, script has {line_count}"
        )
    return result


def dia2_spans(
    chunks: list[tuple[int, int, float]], lines: list[Line], gap_s: float
) -> list[tuple[float, float]]:
    """Chunk edges from the renderer. Each chunk's length is split between its lines by words."""
    spans: list[tuple[float, float]] = [(0.0, 0.0)] * len(lines)
    cursor = 0.0
    for first, count, audio in chunks:
        group = lines[first - 1 : first - 1 + count]
        weights = [max(1, len(line.text.split())) for line in group]
        total = sum(weights)
        at = cursor
        for offset, weight in enumerate(weights):
            length = audio * weight / total
            spans[first - 1 + offset] = (at, at + length)
            at += length
        cursor += audio + gap_s
    return spans


def _result(
    wav: Path,
    engine: str,
    timing: Timing,
    lines: list[Line],
    spans: list[tuple[float, float]],
    duration: float,
) -> RenderResult:
    timings = tuple(
        LineTiming(line.number, line.speaker, round(start, 3), round(end, 3))
        for line, (start, end) in zip(lines, spans, strict=True)
    )
    return RenderResult(wav=wav, engine=engine, timing=timing, lines=timings, duration=duration)


# --- remote command builders -----------------------------------------------------------------


def chatterbox_commands(cfg: ChatterboxConfig, remote_script: str, remote_wav: str) -> list[str]:
    commands = []
    if cfg.hf_home:
        commands.append(f"$env:HF_HOME = {ps_quote(cfg.hf_home)}")
    commands.append(f"Set-Location -LiteralPath {ps_quote(cfg.workdir)}")
    commands.append(
        _invoke([cfg.python, cfg.renderer, cfg.voice_s1, cfg.voice_s2, remote_script, remote_wav])
    )
    return commands


def dia2_commands(cfg: Dia2Config, remote_script: str, remote_wav: str) -> list[str]:
    commands = []
    if cfg.hf_home:
        commands.append(f"$env:HF_HOME = {ps_quote(cfg.hf_home)}")
    commands.append(f"Set-Location -LiteralPath {ps_quote(cfg.workdir)}")
    commands.append(
        _invoke(
            [
                cfg.python,
                cfg.renderer,
                "--script",
                remote_script,
                "--out",
                remote_wav,
                "--ref1",
                cfg.ref_s1,
                "--ref2",
                cfg.ref_s2,
                "--lines-per-chunk",
                str(cfg.lines_per_chunk),
                "--cfg",
                str(cfg.cfg),
                "--temp",
                str(cfg.temp),
                "--gap",
                str(cfg.gap_s),
                "--atempo",
                str(cfg.atempo),
            ]
        )
    )
    return commands


# --- engines ---------------------------------------------------------------------------------


class ChatterboxRemote:
    """Chatterbox Turbo on the GPU box, via the bake-off renderer. Timing is measured."""

    name = "chatterbox"

    def __init__(
        self, box: RemoteConfig, cfg: ChatterboxConfig, runner: Runner = subprocess.run
    ) -> None:
        self.cfg = cfg
        self.box = Box(box, runner)

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        run_dir = self.box.new_run_dir()
        remote_script = win_join(run_dir, "script.txt")
        remote_wav = win_join(run_dir, "voice.wav")
        with tempfile.TemporaryDirectory(prefix="civiccast-chatterbox-") as tmp:
            local_script = Path(tmp) / "script.txt"
            write_renderer_script(lines, local_script)
            self.box.mkdir(run_dir)
            self.box.upload(local_script, remote_script)
        stdout = self.box.run(chatterbox_commands(self.cfg, remote_script, remote_wav))
        durations = parse_chatterbox_durations(stdout, lines)
        self.box.download(remote_wav, out_wav)
        duration = probe_duration(out_wav)
        spans = fit_to_length(spans_from_durations(durations, self.cfg.gap_s), duration)
        return _result(out_wav, self.name, "measured", lines, spans, duration)


class Dia2Remote:
    """Dia2 (chunked, reference-prefixed) on the GPU box, via the bake-off renderer.

    Timing is approximate inside each chunk. See the module docstring.
    """

    name = "dia2"

    def __init__(self, box: RemoteConfig, cfg: Dia2Config, runner: Runner = subprocess.run) -> None:
        self.cfg = cfg
        self.box = Box(box, runner)

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        run_dir = self.box.new_run_dir()
        remote_script = win_join(run_dir, "script.txt")
        stem = win_join(run_dir, "voice")
        remote_wav = stem + ".wav"
        with tempfile.TemporaryDirectory(prefix="civiccast-dia2-") as tmp:
            local_script = Path(tmp) / "script.txt"
            local_json = Path(tmp) / "voice.json"
            write_renderer_script(lines, local_script)
            self.box.mkdir(run_dir)
            self.box.upload(local_script, remote_script)
            self.box.run(dia2_commands(self.cfg, remote_script, remote_wav))
            # The renderer writes the tempo-adjusted copy and a JSON summary next to --out.
            if self.cfg.atempo != 1.0:
                final_remote = f"{stem}-atempo{self.cfg.atempo:.2f}.wav"
            else:
                final_remote = remote_wav
            self.box.download(f"{stem}.json", local_json)
            summary = json.loads(local_json.read_text(encoding="utf-8"))
            self.box.download(final_remote, out_wav)
        chunks = parse_dia2_chunks(summary, len(lines))
        duration = probe_duration(out_wav)
        spans = fit_to_length(dia2_spans(chunks, lines, self.cfg.gap_s), duration)
        return _result(out_wav, self.name, "approximate", lines, spans, duration)


def fake_duration(text: str) -> float:
    """Predictable tone length for FakeEngine: 0.3 s per word, at least 0.5 s."""
    return max(0.5, round(0.3 * len(text.split()), 3))


class FakeEngine:
    """Tones for speech and silence for gaps, written with ffmpeg. Tests only; no speech.

    Speaker S1 is a 440 Hz tone and S2 is 220 Hz. Timing is exact.
    """

    name = "fake"

    def __init__(self, gap_s: float = 0.35, sample_rate: int = 24000) -> None:
        self.gap_s = gap_s
        self.sample_rate = sample_rate

    def render(self, lines: list[Line], out_wav: Path) -> RenderResult:
        if not lines:
            raise EngineError("no spoken lines to render")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        sr = self.sample_rate
        durations = [fake_duration(line.text) for line in lines]
        graph: list[str] = []
        labels: list[str] = []
        for index, (line, duration) in enumerate(zip(lines, durations, strict=True)):
            freq = 440 if line.speaker == "S1" else 220
            graph.append(
                f"sine=frequency={freq}:sample_rate={sr}:duration={duration:.3f}[s{index}]"
            )
            labels.append(f"[s{index}]")
            if index < len(lines) - 1:
                graph.append(
                    f"anullsrc=sample_rate={sr}:channel_layout=mono:duration={self.gap_s:.3f}"
                    f"[g{index}]"
                )
                labels.append(f"[g{index}]")
        graph.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]")
        run_ffmpeg(
            [
                "-filter_complex",
                ";".join(graph),
                "-map",
                "[out]",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ],
            what="fake engine",
        )
        spans = spans_from_durations(durations, self.gap_s)
        return _result(out_wav, self.name, "exact", lines, spans, probe_duration(out_wav))
